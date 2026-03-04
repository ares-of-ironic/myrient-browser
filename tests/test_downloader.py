"""Tests for downloader module."""

import tempfile
from pathlib import Path

import pytest

from myrient_browser.config import Config
from myrient_browser.downloader import check_download_status, get_downloaded_paths


class TestCheckDownloadStatus:
    """Tests for check_download_status function."""

    @pytest.fixture
    def config(self, tmp_path):
        cfg = Config()
        cfg.project_root = tmp_path
        cfg.download.download_dir = "downloads"
        (tmp_path / "downloads").mkdir()
        return cfg

    def test_missing_file(self, config):
        status = check_download_status(config, "nonexistent/file.zip")
        assert status == "MISSING"

    def test_downloaded_file(self, config):
        download_dir = config.project_root / "downloads"
        (download_dir / "test").mkdir(parents=True)
        (download_dir / "test" / "file.zip").write_bytes(b"content")

        status = check_download_status(config, "test/file.zip")
        assert status == "DOWNLOADED"

    def test_empty_file(self, config):
        download_dir = config.project_root / "downloads"
        (download_dir / "test").mkdir(parents=True)
        (download_dir / "test" / "file.zip").write_bytes(b"")

        status = check_download_status(config, "test/file.zip")
        assert status == "MISSING"

    def test_partial_file(self, config):
        download_dir = config.project_root / "downloads"
        (download_dir / "test").mkdir(parents=True)
        (download_dir / "test" / "file.zip.part").write_bytes(b"partial")

        status = check_download_status(config, "test/file.zip")
        assert status == "PARTIAL"


class TestGetDownloadedPaths:
    """Tests for get_downloaded_paths function."""

    @pytest.fixture
    def config(self, tmp_path):
        cfg = Config()
        cfg.project_root = tmp_path
        cfg.download.download_dir = "downloads"
        (tmp_path / "downloads").mkdir()
        return cfg

    def test_get_downloaded(self, config):
        download_dir = config.project_root / "downloads"
        (download_dir / "dir1").mkdir(parents=True)
        (download_dir / "dir1" / "file1.zip").write_bytes(b"content")
        (download_dir / "dir1" / "file2.zip").write_bytes(b"content")

        paths = ["dir1/file1.zip", "dir1/file2.zip", "dir1/file3.zip"]
        downloaded = get_downloaded_paths(config, paths)

        assert "dir1/file1.zip" in downloaded
        assert "dir1/file2.zip" in downloaded
        assert "dir1/file3.zip" not in downloaded
