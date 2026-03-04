"""Tests for configuration module."""

import os
import tempfile
from pathlib import Path

import pytest

from myrient_browser.config import Config, normalize_path, validate_path


class TestNormalizePath:
    """Tests for path normalization."""

    def test_simple_path(self):
        assert normalize_path("foo/bar/baz.zip") == "foo/bar/baz.zip"

    def test_removes_leading_slash(self):
        assert normalize_path("/foo/bar") == "foo/bar"

    def test_removes_trailing_slash(self):
        assert normalize_path("foo/bar/") == "foo/bar"

    def test_removes_double_slashes(self):
        assert normalize_path("foo//bar///baz") == "foo/bar/baz"

    def test_removes_dot_components(self):
        assert normalize_path("foo/./bar/./baz") == "foo/bar/baz"

    def test_removes_dotdot_components(self):
        assert normalize_path("../foo/bar") == "foo/bar"
        assert normalize_path("../../foo") == "foo"
        assert normalize_path("foo/..") == "foo"

    def test_normalizes_backslashes(self):
        assert normalize_path("foo\\bar\\baz") == "foo/bar/baz"

    def test_empty_path(self):
        assert normalize_path("") == ""
        assert normalize_path("/") == ""

    def test_complex_traversal(self):
        assert normalize_path("../../../baz") == "baz"


class TestValidatePath:
    """Tests for path validation."""

    def test_valid_paths(self):
        assert validate_path("foo/bar/baz.zip") is True
        assert validate_path("MAME/ROMs/game.zip") is True
        assert validate_path("folder") is True

    def test_invalid_traversal(self):
        assert validate_path("../foo") is False
        assert validate_path("foo/../bar") is False
        assert validate_path("foo/../../bar") is False

    def test_invalid_absolute(self):
        assert validate_path("/foo/bar") is False

    def test_valid_with_spaces(self):
        assert validate_path("foo bar/baz qux.zip") is True


class TestConfig:
    """Tests for Config class."""

    def test_default_values(self):
        config = Config()
        assert config.server.base_url == "https://myrient.erista.me/files"
        assert config.download.concurrency == 4
        assert config.download.retries == 3

    def test_load_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write("""
[server]
base_url = "https://example.com/files"

[download]
concurrency = 8
""")
            f.flush()

            config = Config.load(Path(f.name))
            assert config.server.base_url == "https://example.com/files"
            assert config.download.concurrency == 8

        os.unlink(f.name)

    def test_load_from_env(self):
        os.environ["MYRIENT_BASE_URL"] = "https://env.example.com"
        os.environ["MYRIENT_CONCURRENCY"] = "16"

        try:
            config = Config.load()
            assert config.server.base_url == "https://env.example.com"
            assert config.download.concurrency == 16
        finally:
            del os.environ["MYRIENT_BASE_URL"]
            del os.environ["MYRIENT_CONCURRENCY"]

    def test_build_url(self):
        config = Config()
        config.server.base_url = "https://example.com/files"

        assert config.build_url("foo/bar.zip") == "https://example.com/files/foo/bar.zip"
        assert config.build_url("/foo/bar.zip") == "https://example.com/files/foo/bar.zip"

    def test_get_local_path(self):
        config = Config()
        config.download.download_dir = "downloads"
        config.project_root = Path("/project")

        local = config.get_local_path("MAME/ROMs/game.zip")
        assert local == Path("/project/downloads/MAME/ROMs/game.zip")
