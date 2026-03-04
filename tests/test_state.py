"""Tests for state management module."""

import json
import tempfile
from pathlib import Path

import pytest

from myrient_browser.config import Config
from myrient_browser.state import DownloadItem, DownloadStatus, QueueState, StateManager


class TestDownloadItem:
    """Tests for DownloadItem class."""

    def test_create_item(self):
        item = DownloadItem(
            path="MAME/ROMs/pacman.zip",
            url="https://example.com/MAME/ROMs/pacman.zip",
            local_path="/downloads/MAME/ROMs/pacman.zip",
        )
        assert item.status == DownloadStatus.QUEUED
        assert item.progress == 0.0
        assert item.retries == 0

    def test_to_dict(self):
        item = DownloadItem(
            path="test.zip",
            url="https://example.com/test.zip",
            local_path="/downloads/test.zip",
            status=DownloadStatus.DOWNLOADING,
            progress=50.0,
        )
        data = item.to_dict()
        assert data["path"] == "test.zip"
        assert data["status"] == "downloading"
        assert data["progress"] == 50.0

    def test_from_dict(self):
        data = {
            "path": "test.zip",
            "url": "https://example.com/test.zip",
            "local_path": "/downloads/test.zip",
            "status": "completed",
            "progress": 100.0,
            "total_size": 1024,
            "downloaded_size": 1024,
            "speed": 0.0,
            "eta": 0.0,
            "error": "",
            "retries": 0,
            "added_at": 1234567890.0,
            "started_at": 1234567891.0,
            "completed_at": 1234567892.0,
            "expanded_from": "",
        }
        item = DownloadItem.from_dict(data)
        assert item.path == "test.zip"
        assert item.status == DownloadStatus.COMPLETED
        assert item.progress == 100.0


class TestQueueState:
    """Tests for QueueState class."""

    def test_to_dict(self):
        state = QueueState()
        item = DownloadItem(
            path="test.zip",
            url="https://example.com/test.zip",
            local_path="/downloads/test.zip",
        )
        state.items["test.zip"] = item

        data = state.to_dict()
        assert "items" in data
        assert "test.zip" in data["items"]

    def test_from_dict(self):
        data = {
            "version": 1,
            "items": {
                "test.zip": {
                    "path": "test.zip",
                    "url": "https://example.com/test.zip",
                    "local_path": "/downloads/test.zip",
                    "status": "queued",
                    "progress": 0.0,
                    "total_size": 0,
                    "downloaded_size": 0,
                    "speed": 0.0,
                    "eta": 0.0,
                    "error": "",
                    "retries": 0,
                    "added_at": 0.0,
                    "started_at": 0.0,
                    "completed_at": 0.0,
                    "expanded_from": "",
                }
            },
        }
        state = QueueState.from_dict(data)
        assert "test.zip" in state.items
        assert state.items["test.zip"].status == DownloadStatus.QUEUED


class TestStateManager:
    """Tests for StateManager class."""

    @pytest.fixture
    def config(self, tmp_path):
        cfg = Config()
        cfg.project_root = tmp_path
        cfg.state.state_file = "state.json"
        return cfg

    @pytest.fixture
    def state_manager(self, config):
        return StateManager(config)

    def test_add_item(self, state_manager):
        item = DownloadItem(
            path="test.zip",
            url="https://example.com/test.zip",
            local_path="/downloads/test.zip",
        )
        state_manager.add_item(item)
        assert state_manager.get_item("test.zip") is not None

    def test_remove_item(self, state_manager):
        item = DownloadItem(
            path="test.zip",
            url="https://example.com/test.zip",
            local_path="/downloads/test.zip",
        )
        state_manager.add_item(item)
        state_manager.remove_item("test.zip")
        assert state_manager.get_item("test.zip") is None

    def test_update_item(self, state_manager):
        item = DownloadItem(
            path="test.zip",
            url="https://example.com/test.zip",
            local_path="/downloads/test.zip",
        )
        state_manager.add_item(item)
        state_manager.update_item("test.zip", status=DownloadStatus.DOWNLOADING, progress=50.0)

        updated = state_manager.get_item("test.zip")
        assert updated.status == DownloadStatus.DOWNLOADING
        assert updated.progress == 50.0

    def test_get_items_by_status(self, state_manager):
        items = [
            DownloadItem(path="q1.zip", url="", local_path="", status=DownloadStatus.QUEUED),
            DownloadItem(path="q2.zip", url="", local_path="", status=DownloadStatus.QUEUED),
            DownloadItem(path="d1.zip", url="", local_path="", status=DownloadStatus.DOWNLOADING),
            DownloadItem(path="c1.zip", url="", local_path="", status=DownloadStatus.COMPLETED),
        ]
        for item in items:
            state_manager.add_item(item)

        queued = state_manager.get_queued_items()
        assert len(queued) == 2

        downloading = state_manager.get_downloading_items()
        assert len(downloading) == 1

    def test_save_and_load(self, state_manager, config):
        item = DownloadItem(
            path="test.zip",
            url="https://example.com/test.zip",
            local_path="/downloads/test.zip",
            status=DownloadStatus.COMPLETED,
        )
        state_manager.add_item(item)
        state_manager.save(force=True)

        new_manager = StateManager(config)
        new_manager.load()

        loaded = new_manager.get_item("test.zip")
        assert loaded is not None
        assert loaded.status == DownloadStatus.COMPLETED

    def test_clear_completed(self, state_manager):
        items = [
            DownloadItem(path="c1.zip", url="", local_path="", status=DownloadStatus.COMPLETED),
            DownloadItem(path="c2.zip", url="", local_path="", status=DownloadStatus.COMPLETED),
            DownloadItem(path="q1.zip", url="", local_path="", status=DownloadStatus.QUEUED),
        ]
        for item in items:
            state_manager.add_item(item)

        removed = state_manager.clear_completed()
        assert removed == 2
        assert len(state_manager.get_all_items()) == 1

    def test_retry_failed(self, state_manager):
        items = [
            DownloadItem(path="f1.zip", url="", local_path="", status=DownloadStatus.FAILED),
            DownloadItem(path="f2.zip", url="", local_path="", status=DownloadStatus.FAILED),
        ]
        for item in items:
            state_manager.add_item(item)

        reset = state_manager.retry_failed()
        assert reset == 2

        queued = state_manager.get_queued_items()
        assert len(queued) == 2

    def test_get_stats(self, state_manager):
        items = [
            DownloadItem(path="q1.zip", url="", local_path="", status=DownloadStatus.QUEUED),
            DownloadItem(path="d1.zip", url="", local_path="", status=DownloadStatus.DOWNLOADING),
            DownloadItem(path="c1.zip", url="", local_path="", status=DownloadStatus.COMPLETED),
            DownloadItem(path="f1.zip", url="", local_path="", status=DownloadStatus.FAILED),
        ]
        for item in items:
            state_manager.add_item(item)

        stats = state_manager.get_stats()
        assert stats["total"] == 4
        assert stats["queued"] == 1
        assert stats["downloading"] == 1
        assert stats["completed"] == 1
        assert stats["failed"] == 1

    def test_clear_all(self, state_manager):
        items = [
            DownloadItem(path="q1.zip", url="", local_path="", status=DownloadStatus.QUEUED),
            DownloadItem(path="d1.zip", url="", local_path="", status=DownloadStatus.DOWNLOADING),
            DownloadItem(path="c1.zip", url="", local_path="", status=DownloadStatus.COMPLETED),
            DownloadItem(path="f1.zip", url="", local_path="", status=DownloadStatus.FAILED),
        ]
        for item in items:
            state_manager.add_item(item)

        assert len(state_manager.get_all_items()) == 4

        removed = state_manager.clear_all()
        assert removed == 4
        assert len(state_manager.get_all_items()) == 0
        assert state_manager.is_empty
