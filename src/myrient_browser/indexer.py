"""Index management - parsing, tree building, and searching."""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Lock, Thread
from typing import Callable, Iterator

from rapidfuzz import fuzz, process

from .config import Config


def format_size(size: int, use_decimal: bool = False) -> str:
    """Format size in bytes to human readable string.
    
    Args:
        size: Size in bytes
        use_decimal: If True, use decimal units (1 KB = 1000 B).
                     If False, use binary units (1 KB = 1024 B).
    """
    if size < 0:
        return "-"
    if size == 0:
        return "0 B"
    
    base = 1000 if use_decimal else 1024
    
    if size < base:
        return f"{size} B"
    if size < base ** 2:
        return f"{size / base:.1f} KB"
    if size < base ** 3:
        return f"{size / base ** 2:.1f} MB"
    if size < base ** 4:
        return f"{size / base ** 3:.2f} GB"
    return f"{size / base ** 4:.2f} TB"


class IndexNode:
    """A node in the file tree (file or directory)."""
    
    __slots__ = ('name', 'path', 'is_dir', 'parent', 'children', 'size')

    def __init__(
        self,
        name: str,
        path: str,
        is_dir: bool,
        parent: IndexNode | None = None,
        size: int = -1,
    ):
        self.name = name
        self.path = path
        self.is_dir = is_dir
        self.parent = parent
        self.children: dict[str, IndexNode] = {}
        self.size = size

    @property
    def full_path(self) -> str:
        """Get full path from root."""
        return self.path

    def get_all_files(self) -> Iterator[IndexNode]:
        """Recursively yield all file nodes under this node."""
        if not self.is_dir:
            yield self
        else:
            for child in self.children.values():
                yield from child.get_all_files()

    def get_all_nodes(self) -> Iterator[IndexNode]:
        """Recursively yield all nodes (files and dirs) under this node."""
        yield self
        if self.is_dir:
            for child in self.children.values():
                yield from child.get_all_nodes()

    def count_files(self) -> int:
        """Count total files under this node."""
        if not self.is_dir:
            return 1
        return sum(child.count_files() for child in self.children.values())

    def count_children(self) -> int:
        """Count direct children."""
        return len(self.children)

    def total_size(self) -> int:
        """Calculate total size of this node and all children.
        
        Returns -1 if any file has unknown size.
        """
        if not self.is_dir:
            return self.size
        
        total = 0
        for child in self.children.values():
            child_size = child.total_size()
            if child_size < 0:
                return -1
            total += child_size
        return total

    def format_size(self) -> str:
        """Get formatted size string."""
        if self.is_dir:
            return format_size(self.total_size())
        return format_size(self.size)


class FileIndex:
    """Manages the file index with tree structure and search capabilities.
    
    Uses lazy tree building - only builds tree nodes when needed for navigation.
    """

    def __init__(self, config: Config):
        self.config = config
        self.root = IndexNode(name="", path="", is_dir=True)
        self.all_paths: list[str] = []
        self.path_to_node: dict[str, IndexNode] = {}
        self._path_info: dict[str, tuple[bool, int]] = {}  # path -> (is_dir, size)
        self._children_cache: dict[str, list[str]] = {}  # parent_path -> [child_paths]
        self._lock = Lock()
        self._last_mtime: float = 0
        self._watcher_thread: Thread | None = None
        self._watcher_running = False
        self._on_reload_callbacks: list[Callable[[], None]] = []
        self._has_sizes = False
        self._tree_built = False
        self._dir_size_cache: dict[str, int] = {}

    @property
    def has_sizes(self) -> bool:
        """Check if index has size information."""
        return self._has_sizes

    def load(self) -> None:
        """Load index from file."""
        index_path = self.config.get_index_path()
        if not index_path.exists():
            raise FileNotFoundError(f"Index file not found: {index_path}")

        with self._lock:
            # Reset state
            self.root = IndexNode(name="", path="", is_dir=True)
            self.all_paths = []
            self.path_to_node = {"": self.root}
            self._path_info = {}
            self._children_cache = {}
            self._dir_size_cache = {}
            self._tree_built = False
            
            # Detect format by extension or content
            if index_path.suffix.lower() == ".json":
                self._load_json_index(index_path)
            else:
                # Try to detect JSON by first character
                with open(index_path, encoding="utf-8", errors="replace") as f:
                    first_char = f.read(1)
                    if first_char == "[":
                        self._load_json_index(index_path)
                    else:
                        self._load_text_index(index_path)
            
            self._last_mtime = index_path.stat().st_mtime

    def _load_json_index(self, index_path: Path) -> None:
        """Load index from JSON file (rclone lsjson format)."""
        self._has_sizes = True

        with open(index_path, encoding="utf-8", errors="replace") as f:
            data = json.load(f)

        # Identify directories
        dir_paths: set[str] = set()
        
        for item in data:
            raw_path = item.get("Path", "")
            if not raw_path:
                continue
            
            path = raw_path.strip("/")
            if not path:
                continue
            
            is_dir = item.get("IsDir", False)
            if is_dir:
                dir_paths.add(path)
            
            # Add parent directories
            slash_idx = path.rfind("/")
            while slash_idx > 0:
                parent = path[:slash_idx]
                if parent in dir_paths:
                    break
                dir_paths.add(parent)
                slash_idx = parent.rfind("/")

        # Store path info
        for item in data:
            raw_path = item.get("Path", "")
            if not raw_path:
                continue
            
            path = raw_path.strip("/")
            if not path:
                continue
            
            is_dir = item.get("IsDir", False) or path in dir_paths
            size = item.get("Size", -1) if not is_dir else -1
            
            self.all_paths.append(path)
            self._path_info[path] = (is_dir, size)
            
            # Build children cache
            slash_idx = path.rfind("/")
            parent = path[:slash_idx] if slash_idx > 0 else ""
            if parent not in self._children_cache:
                self._children_cache[parent] = []
            self._children_cache[parent].append(path)
            
            # Accumulate size to all parent directories
            if not is_dir and size > 0:
                current = parent
                while True:
                    if current not in self._dir_size_cache:
                        self._dir_size_cache[current] = 0
                    self._dir_size_cache[current] += size
                    if not current:
                        break
                    slash_idx = current.rfind("/")
                    current = current[:slash_idx] if slash_idx > 0 else ""

    def _load_text_index(self, index_path: Path) -> None:
        """Load index from text file (one path per line)."""
        self._has_sizes = False

        # Identify directories
        dir_paths: set[str] = set()
        raw_entries: list[tuple[str, bool]] = []

        with open(index_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                raw_path = line.rstrip("\n\r")
                if not raw_path:
                    continue

                is_dir = raw_path.endswith("/")
                path = raw_path.strip("/")
                if not path:
                    continue

                raw_entries.append((path, is_dir))
                
                if is_dir:
                    dir_paths.add(path)
                
                # Add parent directories
                slash_idx = path.rfind("/")
                while slash_idx > 0:
                    parent = path[:slash_idx]
                    if parent in dir_paths:
                        break
                    dir_paths.add(parent)
                    slash_idx = parent.rfind("/")

        # Store path info
        for path, is_dir in raw_entries:
            actual_is_dir = is_dir or path in dir_paths
            
            self.all_paths.append(path)
            self._path_info[path] = (actual_is_dir, -1)
            
            # Build children cache
            slash_idx = path.rfind("/")
            parent = path[:slash_idx] if slash_idx > 0 else ""
            if parent not in self._children_cache:
                self._children_cache[parent] = []
            self._children_cache[parent].append(path)

    def _ensure_node(self, path: str) -> IndexNode | None:
        """Ensure a node exists in the tree, creating it lazily if needed."""
        if not path:
            return self.root
            
        if path in self.path_to_node:
            return self.path_to_node[path]
        
        if path not in self._path_info:
            return None
        
        is_dir, size = self._path_info[path]
        
        # Ensure parent exists
        slash_idx = path.rfind("/")
        parent_path = path[:slash_idx] if slash_idx > 0 else ""
        parent = self._ensure_node(parent_path)
        
        if parent is None:
            return None
        
        name = path[slash_idx + 1:] if slash_idx >= 0 else path
        
        node = IndexNode(
            name=name,
            path=path,
            is_dir=is_dir,
            parent=parent,
            size=size,
        )
        parent.children[name] = node
        self.path_to_node[path] = node
        
        return node

    def _ensure_children(self, path: str) -> None:
        """Ensure all children of a path are loaded into the tree."""
        parent = self._ensure_node(path) if path else self.root
        if parent is None:
            return
        
        child_paths = self._children_cache.get(path, [])
        for child_path in child_paths:
            self._ensure_node(child_path)

    def reload(self) -> bool:
        """Reload index from file if changed.

        Returns True if index was reloaded.
        """
        index_path = self.config.get_index_path()
        if not index_path.exists():
            return False

        current_mtime = index_path.stat().st_mtime
        if current_mtime <= self._last_mtime:
            return False

        self.load()
        for callback in self._on_reload_callbacks:
            try:
                callback()
            except Exception:
                pass
        return True

    def on_reload(self, callback: Callable[[], None]) -> None:
        """Register a callback to be called when index is reloaded."""
        self._on_reload_callbacks.append(callback)

    def start_watcher(self) -> None:
        """Start background thread to watch for index changes."""
        if self._watcher_thread is not None:
            return

        self._watcher_running = True
        self._watcher_thread = Thread(target=self._watch_loop, daemon=True)
        self._watcher_thread.start()

    def stop_watcher(self) -> None:
        """Stop the background watcher thread."""
        self._watcher_running = False
        if self._watcher_thread is not None:
            self._watcher_thread.join(timeout=2)
            self._watcher_thread = None

    def _watch_loop(self) -> None:
        """Background loop to check for index changes."""
        while self._watcher_running:
            try:
                self.reload()
            except Exception:
                pass
            time.sleep(self.config.index.watch_interval)

    def get_node(self, path: str) -> IndexNode | None:
        """Get node by path."""
        path = path.strip("/")
        return self._ensure_node(path)
    
    def get_dir_size(self, path: str) -> int:
        """Get total size of a directory from cache.
        
        Returns -1 if sizes not available.
        """
        path = path.strip("/")
        info = self._path_info.get(path)
        if info is None:
            return -1
        
        is_dir, size = info
        if not is_dir:
            return size
        
        # Use cached directory size
        return self._dir_size_cache.get(path, 0)

    def get_children(self, path: str = "") -> list[IndexNode]:
        """Get children of a directory."""
        path = path.strip("/")
        self._ensure_children(path)
        
        node = self._ensure_node(path) if path else self.root
        if node is None or not node.is_dir:
            return []
        return sorted(node.children.values(), key=lambda n: (not n.is_dir, n.name.lower()))

    def search(
        self,
        query: str,
        limit: int = 100,
        dirs_only: bool = False,
        files_only: bool = False,
    ) -> list[IndexNode]:
        """Search for paths matching query.

        Supports:
        - Simple substring matching
        - Fuzzy matching with rapidfuzz
        - Multiple terms separated by |
        """
        if not query:
            return []

        query = query.strip()
        terms = [t.strip().lower() for t in query.split("|") if t.strip()]

        if not terms:
            return []

        with self._lock:
            candidates = self.all_paths

        if dirs_only:
            candidates = [p for p in candidates if self._path_info.get(p, (False, -1))[0]]
        elif files_only:
            candidates = [p for p in candidates if not self._path_info.get(p, (True, -1))[0]]

        matches: set[str] = set()

        for term in terms:
            term_matches = self._search_term(term, candidates, limit * 2)
            matches.update(term_matches)

        result_nodes = []
        for path in matches:
            node = self._ensure_node(path)
            if node:
                result_nodes.append(node)

        result_nodes.sort(key=lambda n: (not n.is_dir, n.path.lower()))
        return result_nodes[:limit]

    def _search_term(self, term: str, candidates: list[str], limit: int) -> list[str]:
        """Search for a single term."""
        exact_matches = []
        for path in candidates:
            if term in path.lower():
                exact_matches.append(path)
                if len(exact_matches) >= limit:
                    break

        if len(exact_matches) >= limit // 4:
            return exact_matches[:limit]

        fuzzy_results = process.extract(
            term,
            candidates,
            scorer=fuzz.partial_ratio,
            limit=limit,
            score_cutoff=70,
        )

        fuzzy_matches = [path for path, score, _ in fuzzy_results]

        combined = list(dict.fromkeys(exact_matches + fuzzy_matches))
        return combined[:limit]

    def expand_selection(self, paths: list[str]) -> list[str]:
        """Expand directories to their contained files.

        Returns list of file paths only (no directories).
        """
        result: list[str] = []
        seen: set[str] = set()

        for path in paths:
            path = path.strip("/")
            info = self._path_info.get(path)
            if info is None:
                continue
            
            is_dir, _ = info
            if not is_dir:
                if path not in seen:
                    seen.add(path)
                    result.append(path)
            else:
                # Expand directory - find all files with this prefix
                prefix = path + "/"
                for p in self.all_paths:
                    if p.startswith(prefix) or p == path:
                        p_info = self._path_info.get(p)
                        if p_info and not p_info[0]:  # is file
                            if p not in seen:
                                seen.add(p)
                                result.append(p)

        return result

    def get_selection_size(self, paths: list[str]) -> int:
        """Calculate total size of selected paths.
        
        Returns -1 if any file has unknown size.
        """
        total = 0
        seen: set[str] = set()

        for path in paths:
            path = path.strip("/")
            info = self._path_info.get(path)
            if info is None:
                continue
            
            is_dir, size = info
            if not is_dir:
                if path not in seen:
                    seen.add(path)
                    if size < 0:
                        return -1
                    total += size
            else:
                # Expand directory
                prefix = path + "/"
                for p in self.all_paths:
                    if p.startswith(prefix):
                        p_info = self._path_info.get(p)
                        if p_info and not p_info[0]:  # is file
                            if p not in seen:
                                seen.add(p)
                                if p_info[1] < 0:
                                    return -1
                                total += p_info[1]

        return total

    @property
    def total_entries(self) -> int:
        """Total number of entries in index."""
        return len(self.all_paths)

    @property
    def total_files(self) -> int:
        """Total number of files (non-directories)."""
        return sum(1 for p in self.all_paths if not self._path_info.get(p, (True, -1))[0])

    @property
    def total_dirs(self) -> int:
        """Total number of directories."""
        return sum(1 for p in self.all_paths if self._path_info.get(p, (False, -1))[0])

    @property
    def total_size(self) -> int:
        """Total size of all files. Returns -1 if sizes unknown."""
        if not self._has_sizes:
            return -1
        
        total = 0
        for path in self.all_paths:
            info = self._path_info.get(path)
            if info:
                is_dir, size = info
                if not is_dir:
                    if size < 0:
                        return -1
                    total += size
        return total
