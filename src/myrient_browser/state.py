"""State management for download queue persistence."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Lock, Thread, Event
from typing import Any

from .config import Config

logger = logging.getLogger(__name__)


class DownloadStatus(str, Enum):
    """Status of a download item."""

    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    ALREADY_DOWNLOADED = "already_downloaded"  # file exists locally; skipped unless forced


@dataclass
class DownloadItem:
    """Represents a single download item."""

    path: str
    url: str
    local_path: str
    status: DownloadStatus = DownloadStatus.QUEUED
    progress: float = 0.0
    total_size: int = 0
    downloaded_size: int = 0
    speed: float = 0.0
    eta: float = 0.0
    error: str = ""
    retries: int = 0
    added_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0
    expanded_from: str = ""
    priority: int = 0  # lower = downloaded sooner; 0 = normal, negative = high priority
    local_size: int = 0  # size of existing local file at queue-add time (0 if absent)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "path": self.path,
            "url": self.url,
            "local_path": self.local_path,
            "status": self.status.value,
            "progress": self.progress,
            "total_size": self.total_size,
            "downloaded_size": self.downloaded_size,
            "speed": self.speed,
            "eta": self.eta,
            "error": self.error,
            "retries": self.retries,
            "added_at": self.added_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "expanded_from": self.expanded_from,
            "priority": self.priority,
            "local_size": self.local_size,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DownloadItem:
        """Create from dictionary."""
        data = data.copy()
        data["status"] = DownloadStatus(data.get("status", "queued"))
        data.setdefault("priority", 0)    # backwards-compatible load
        data.setdefault("local_size", 0)  # backwards-compatible load
        return cls(**data)


@dataclass
class QueueState:
    """State of the download queue."""

    items: dict[str, DownloadItem] = field(default_factory=dict)
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "version": self.version,
            "items": {path: item.to_dict() for path, item in self.items.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueueState:
        """Create from dictionary."""
        items = {}
        for path, item_data in data.get("items", {}).items():
            items[path] = DownloadItem.from_dict(item_data)
        return cls(items=items, version=data.get("version", 1))


class StateManager:
    """Manages persistent state for download queue."""

    # Minimum interval between disk writes (seconds)
    _SAVE_DEBOUNCE = 5.0

    def __init__(self, config: Config):
        self.config = config
        self.state = QueueState()
        self._lock = Lock()
        self._dirty = False
        self._stats: dict[str, int] = {
            "total": 0,
            "queued": 0,
            "downloading": 0,
            "completed": 0,
            "failed": 0,
            "paused": 0,
            "already_downloaded": 0,
        }
        # Index: status -> set of paths (for O(1) lookups by status)
        self._by_status: dict[str, set[str]] = {
            "queued": set(),
            "downloading": set(),
            "completed": set(),
            "failed": set(),
            "paused": set(),
            "already_downloaded": set(),
        }
        # Background save thread
        self._save_requested = Event()
        self._save_force = False
        self._save_thread: Thread | None = None
        self._save_running = False
        self._last_save_time = 0.0

    def _rebuild_stats(self) -> None:
        """Rebuild stats cache and status index from scratch (call while holding _lock)."""
        self._stats = {s: 0 for s in ("total", "queued", "downloading", "completed", "failed", "paused", "already_downloaded")}
        self._by_status = {s: set() for s in ("queued", "downloading", "completed", "failed", "paused", "already_downloaded")}
        for path, item in self.state.items.items():
            self._stats["total"] += 1
            self._stats[item.status.value] += 1
            self._by_status[item.status.value].add(path)

    def _stats_add(self, item: DownloadItem) -> None:
        """Increment stats for a newly added item (call while holding _lock)."""
        self._stats["total"] += 1
        self._stats[item.status.value] += 1
        self._by_status[item.status.value].add(item.path)

    def _stats_remove(self, item: DownloadItem) -> None:
        """Decrement stats for a removed item (call while holding _lock)."""
        self._stats["total"] -= 1
        self._stats[item.status.value] -= 1
        self._by_status[item.status.value].discard(item.path)

    def _stats_change_status(self, old_status: DownloadStatus, new_status: DownloadStatus, path: str) -> None:
        """Update stats and index when an item changes status (call while holding _lock)."""
        self._stats[old_status.value] -= 1
        self._stats[new_status.value] += 1
        self._by_status[old_status.value].discard(path)
        self._by_status[new_status.value].add(path)

    def load(self) -> None:
        """Load state from file.
        
        If the main state file is corrupted, attempts to load from backup.
        """
        state_path = self.config.get_state_path()
        backup_path = state_path.with_suffix(".json.auto_backup")
        
        if not state_path.exists():
            # Try backup if main file doesn't exist
            if backup_path.exists():
                logger.warning("Main state file missing, trying auto backup...")
                state_path = backup_path
            else:
                return

        try:
            with open(state_path, encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self.state = QueueState.from_dict(data)
                self._rebuild_stats()
            logger.info(f"Loaded {len(self.state.items)} items from state")
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"Failed to load state from {state_path}: {e}")
            # Try backup
            if backup_path.exists() and state_path != backup_path:
                logger.warning("Trying auto backup...")
                try:
                    with open(backup_path, encoding="utf-8") as f:
                        data = json.load(f)
                    with self._lock:
                        self.state = QueueState.from_dict(data)
                        self._rebuild_stats()
                    logger.info(f"Recovered {len(self.state.items)} items from backup")
                    return
                except (json.JSONDecodeError, KeyError, TypeError, OSError) as e2:
                    logger.error(f"Backup also failed: {e2}")
            self.state = QueueState()

    def _start_save_thread(self) -> None:
        """Start the background save thread if not already running."""
        if self._save_thread is not None and self._save_thread.is_alive():
            return
        self._save_running = True
        self._save_thread = Thread(target=self._save_loop, daemon=True, name="state-saver")
        self._save_thread.start()

    def _save_loop(self) -> None:
        """Background thread that coalesces save requests."""
        while self._save_running:
            # Wait for a save request (or timeout for periodic check)
            triggered = self._save_requested.wait(timeout=2.0)
            if not triggered:
                continue
            self._save_requested.clear()

            # Debounce: wait until enough time has passed since last save
            since_last = time.time() - self._last_save_time
            if since_last < self._SAVE_DEBOUNCE and not self._save_force:
                # Sleep the remaining debounce time, then save
                time.sleep(max(0, self._SAVE_DEBOUNCE - since_last))

            self._save_force = False
            self._do_save()

    def _do_save(self) -> None:
        """Perform the actual save to disk (runs in background thread).
        
        Uses atomic write pattern: write to temp file, fsync, then rename.
        Creates automatic backup before overwriting.
        """
        import shutil
        import threading
        
        state_path = self.config.get_state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path = state_path.with_suffix(".json.auto_backup")

        with self._lock:
            data = self.state.to_dict()
            self._dirty = False

        # Use unique tmp file name to avoid race conditions between threads
        thread_id = threading.current_thread().ident or 0
        tmp_path = state_path.with_name(f"{state_path.name}.{thread_id}.tmp")
        try:
            # Write to temp file first
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            
            # Create backup of current state before replacing (only if main file exists)
            if state_path.exists():
                try:
                    shutil.copy2(state_path, backup_path)
                except OSError:
                    pass  # Backup failure is not critical
            
            # Atomic replace - this is a single syscall, safe from interruption
            tmp_path.replace(state_path)
            
            self._last_save_time = time.time()
            logger.debug(f"State saved: {len(data.get('items', {}))} items")
        except OSError as e:
            logger.error(f"Failed to save state: {e}")
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def save(self, force: bool = False) -> None:
        """Request state save (non-blocking, coalesced in background thread).
        
        Multiple rapid save() calls are debounced into a single disk write.
        """
        if not force and not self._dirty:
            return

        self._start_save_thread()
        if force:
            self._save_force = True
        self._save_requested.set()

    def save_sync(self) -> None:
        """Save state synchronously (for CLI/shutdown). Blocks until complete."""
        self._dirty = True
        self._do_save()

    def add_item(self, item: DownloadItem) -> None:
        """Add item to queue."""
        with self._lock:
            existing = self.state.items.get(item.path)
            if existing is not None:
                self._stats_remove(existing)
            self.state.items[item.path] = item
            self._stats_add(item)
            self._dirty = True

    def remove_item(self, path: str) -> None:
        """Remove item from queue."""
        with self._lock:
            item = self.state.items.pop(path, None)
            if item is not None:
                self._stats_remove(item)
                self._dirty = True

    def update_item(self, path: str, **kwargs: Any) -> None:
        """Update item properties."""
        with self._lock:
            if path in self.state.items:
                item = self.state.items[path]
                old_status = item.status
                new_status = kwargs.get("status")
                for key, value in kwargs.items():
                    if hasattr(item, key):
                        setattr(item, key, value)
                # Update stats and index if status changed
                if new_status is not None and new_status != old_status:
                    self._stats_change_status(old_status, new_status, path)
                self._dirty = True

    def get_item(self, path: str) -> DownloadItem | None:
        """Get item by path."""
        with self._lock:
            return self.state.items.get(path)

    def get_items_by_status(self, status: DownloadStatus) -> list[DownloadItem]:
        """Get all items with given status (O(k) where k = items with that status)."""
        with self._lock:
            paths = self._by_status.get(status.value, set())
            return [self.state.items[p] for p in paths if p in self.state.items]

    def get_queued_items(self, limit: int = 0) -> list[DownloadItem]:
        """Get queued items sorted by priority (lowest value first), then by added_at.
        
        Args:
            limit: If > 0, return only the first N items (uses heapq for O(n + k*log(k)))
        """
        import heapq
        with self._lock:
            paths = self._by_status.get("queued", set())
            items = [self.state.items[p] for p in paths if p in self.state.items]
        
        if limit > 0 and limit < len(items):
            return heapq.nsmallest(limit, items, key=lambda i: (i.priority, i.added_at))
        
        items.sort(key=lambda i: (i.priority, i.added_at))
        return items

    def promote_item(self, path: str) -> bool:
        """Move item to the front of the queue by giving it the highest priority.

        Returns True if the item was found and promoted.
        """
        with self._lock:
            item = self.state.items.get(path)
            if item is None:
                return False
            # Find the minimum priority from queued items only (using index)
            queued_paths = self._by_status.get("queued", set())
            min_priority = min(
                (self.state.items[p].priority for p in queued_paths if p in self.state.items),
                default=0,
            )
            item.priority = min_priority - 1
            self._dirty = True
        return True

    def get_downloading_items(self) -> list[DownloadItem]:
        """Get all currently downloading items (O(k) where k = downloading count)."""
        return self.get_items_by_status(DownloadStatus.DOWNLOADING)

    def get_completed_items(self) -> list[DownloadItem]:
        """Get all completed items."""
        return self.get_items_by_status(DownloadStatus.COMPLETED)

    def get_failed_items(self) -> list[DownloadItem]:
        """Get all failed items."""
        return self.get_items_by_status(DownloadStatus.FAILED)

    def get_all_items(self) -> list[DownloadItem]:
        """Get all items."""
        with self._lock:
            return list(self.state.items.values())

    def get_active_items(self) -> list[DownloadItem]:
        """Get only active items (queued, downloading, failed, paused) - excludes completed/already_downloaded.
        
        This is much faster for large queues with many completed items.
        """
        with self._lock:
            result = []
            for status in ("queued", "downloading", "failed", "paused"):
                for path in self._by_status.get(status, set()):
                    if path in self.state.items:
                        result.append(self.state.items[path])
            return result

    def clear_completed(self) -> int:
        """Remove all completed items. Returns count removed."""
        with self._lock:
            paths_to_remove = list(self._by_status.get("completed", set()))
            count = 0
            for path in paths_to_remove:
                item = self.state.items.pop(path, None)
                if item:
                    self._stats_remove(item)
                    count += 1
            if count > 0:
                self._dirty = True
            return count

    def clear_failed(self) -> int:
        """Remove all failed items. Returns count removed."""
        with self._lock:
            paths_to_remove = list(self._by_status.get("failed", set()))
            count = 0
            for path in paths_to_remove:
                item = self.state.items.pop(path, None)
                if item:
                    self._stats_remove(item)
                    count += 1
            if count > 0:
                self._dirty = True
            return count

    def retry_failed(self) -> int:
        """Reset failed items to queued. Returns count reset."""
        with self._lock:
            paths_to_retry = list(self._by_status.get("failed", set()))
            count = 0
            for path in paths_to_retry:
                item = self.state.items.get(path)
                if item and item.status == DownloadStatus.FAILED:
                    self._stats_change_status(DownloadStatus.FAILED, DownloadStatus.QUEUED, path)
                    item.status = DownloadStatus.QUEUED
                    item.error = ""
                    item.retries = 0
                    count += 1
            if count > 0:
                self._dirty = True
            return count

    def clear_all(self) -> int:
        """Clear all items from queue. Returns count removed."""
        with self._lock:
            count = len(self.state.items)
            self.state.items.clear()
            self._rebuild_stats()
            if count > 0:
                self._dirty = True
            return count

    def get_stats(self) -> dict[str, int]:
        """Get queue statistics (O(1) - uses incremental cache)."""
        with self._lock:
            return {k: max(0, v) for k, v in self._stats.items()}

    def rebuild_stats(self) -> None:
        """Force rebuild of stats cache from actual items (public API)."""
        with self._lock:
            self._rebuild_stats()

    @property
    def is_empty(self) -> bool:
        """Check if queue is empty."""
        with self._lock:
            return len(self.state.items) == 0

    @property
    def has_pending(self) -> bool:
        """Check if there are pending downloads (O(1) using index)."""
        with self._lock:
            return bool(self._by_status.get("queued") or self._by_status.get("downloading"))

    def shutdown(self) -> None:
        """Stop background save thread and flush pending changes to disk."""
        self._save_running = False
        self._save_requested.set()  # wake the thread
        if self._save_thread is not None and self._save_thread.is_alive():
            self._save_thread.join(timeout=5)
        # Final synchronous save to ensure nothing is lost
        if self._dirty:
            self._do_save()
