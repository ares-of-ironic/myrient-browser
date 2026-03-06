"""State management for download queue persistence."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any

from .config import Config


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
        data = asdict(self)
        data["status"] = self.status.value
        return data

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
        """Load state from file."""
        state_path = self.config.get_state_path()
        if not state_path.exists():
            return

        try:
            with open(state_path, encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self.state = QueueState.from_dict(data)
                self._rebuild_stats()
        except (json.JSONDecodeError, KeyError, TypeError):
            self.state = QueueState()

    def save(self, force: bool = False) -> None:
        """Save state to file."""
        if not force and not self._dirty:
            return

        state_path = self.config.get_state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            data = self.state.to_dict()
            self._dirty = False

        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

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
