"""Tests for indexer module."""

import json
import tempfile
from pathlib import Path

import pytest

from myrient_browser.config import Config
from myrient_browser.indexer import FileIndex, IndexNode, format_size


@pytest.fixture
def sample_index_content():
    return """MAME/
MAME/ROMs/
MAME/ROMs/pacman.zip
MAME/ROMs/galaga.zip
MAME/CHDs/
MAME/CHDs/game1.chd
No-Intro/
No-Intro/Nintendo - Game Boy/
No-Intro/Nintendo - Game Boy/Tetris.zip
No-Intro/Commodore - 64/
No-Intro/Commodore - 64/game1.zip
No-Intro/Commodore - 64/game2.zip
"""


@pytest.fixture
def index_file(sample_index_content):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(sample_index_content)
        f.flush()
        yield Path(f.name)
    Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def config(index_file):
    cfg = Config()
    cfg.index.index_file = str(index_file)
    cfg.project_root = index_file.parent
    return cfg


@pytest.fixture
def file_index(config, index_file):
    config.index.index_file = index_file.name
    idx = FileIndex(config)
    idx.load()
    return idx


class TestIndexNode:
    """Tests for IndexNode class."""

    def test_node_creation(self):
        node = IndexNode(name="test", path="foo/test", is_dir=False)
        assert node.name == "test"
        assert node.path == "foo/test"
        assert node.is_dir is False

    def test_get_all_files(self):
        root = IndexNode(name="", path="", is_dir=True)
        dir1 = IndexNode(name="dir1", path="dir1", is_dir=True, parent=root)
        file1 = IndexNode(name="file1.zip", path="dir1/file1.zip", is_dir=False, parent=dir1)
        file2 = IndexNode(name="file2.zip", path="dir1/file2.zip", is_dir=False, parent=dir1)
        dir1.children = {"file1.zip": file1, "file2.zip": file2}
        root.children = {"dir1": dir1}

        files = list(root.get_all_files())
        assert len(files) == 2
        assert all(not f.is_dir for f in files)

    def test_count_files(self):
        root = IndexNode(name="", path="", is_dir=True)
        dir1 = IndexNode(name="dir1", path="dir1", is_dir=True, parent=root)
        file1 = IndexNode(name="file1.zip", path="dir1/file1.zip", is_dir=False, parent=dir1)
        file2 = IndexNode(name="file2.zip", path="dir1/file2.zip", is_dir=False, parent=dir1)
        dir1.children = {"file1.zip": file1, "file2.zip": file2}
        root.children = {"dir1": dir1}

        assert root.count_files() == 2
        assert dir1.count_files() == 2
        assert file1.count_files() == 1


class TestFileIndex:
    """Tests for FileIndex class."""

    def test_load_index(self, file_index):
        assert file_index.total_entries > 0

    def test_get_root_children(self, file_index):
        children = file_index.get_children("")
        names = [c.name for c in children]
        assert "MAME" in names
        assert "No-Intro" in names

    def test_get_nested_children(self, file_index):
        children = file_index.get_children("MAME/ROMs")
        names = [c.name for c in children]
        assert "pacman.zip" in names
        assert "galaga.zip" in names

    def test_get_node(self, file_index):
        node = file_index.get_node("MAME/ROMs/pacman.zip")
        assert node is not None
        assert node.name == "pacman.zip"
        assert node.is_dir is False

    def test_get_node_directory(self, file_index):
        node = file_index.get_node("MAME/ROMs")
        assert node is not None
        assert node.is_dir is True

    def test_get_nonexistent_node(self, file_index):
        node = file_index.get_node("nonexistent/path")
        assert node is None

    def test_search_exact(self, file_index):
        results = file_index.search("pacman")
        paths = [r.path for r in results]
        assert any("pacman" in p for p in paths)

    def test_search_fuzzy(self, file_index):
        results = file_index.search("tetris")
        paths = [r.path for r in results]
        assert any("Tetris" in p for p in paths)

    def test_search_or(self, file_index):
        results = file_index.search("pacman|galaga")
        paths = [r.path for r in results]
        assert any("pacman" in p for p in paths)
        assert any("galaga" in p for p in paths)

    def test_search_commodore(self, file_index):
        results = file_index.search("commodore")
        assert len(results) > 0
        assert any("Commodore" in r.path for r in results)

    def test_expand_selection_file(self, file_index):
        expanded = file_index.expand_selection(["MAME/ROMs/pacman.zip"])
        assert "MAME/ROMs/pacman.zip" in expanded

    def test_expand_selection_directory(self, file_index):
        expanded = file_index.expand_selection(["MAME/ROMs"])
        assert "MAME/ROMs/pacman.zip" in expanded
        assert "MAME/ROMs/galaga.zip" in expanded
        assert "MAME/ROMs" not in expanded

    def test_expand_selection_nested(self, file_index):
        expanded = file_index.expand_selection(["No-Intro/Commodore - 64"])
        assert "No-Intro/Commodore - 64/game1.zip" in expanded
        assert "No-Intro/Commodore - 64/game2.zip" in expanded

    def test_children_sorted(self, file_index):
        children = file_index.get_children("")
        dirs = [c for c in children if c.is_dir]
        files = [c for c in children if not c.is_dir]
        assert children == dirs + files


class TestJsonIndex:
    """Tests for JSON index format (rclone lsjson)."""

    @pytest.fixture
    def json_index_content(self):
        return [
            {"Path": "MAME", "IsDir": True},
            {"Path": "MAME/ROMs", "IsDir": True},
            {"Path": "MAME/ROMs/pacman.zip", "IsDir": False, "Size": 1048576},
            {"Path": "MAME/ROMs/galaga.zip", "IsDir": False, "Size": 2097152},
            {"Path": "No-Intro", "IsDir": True},
            {"Path": "No-Intro/game.zip", "IsDir": False, "Size": 512000},
        ]

    @pytest.fixture
    def json_index_file(self, json_index_content):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(json_index_content, f)
            f.flush()
            yield Path(f.name)
        Path(f.name).unlink(missing_ok=True)

    @pytest.fixture
    def json_file_index(self, json_index_file):
        cfg = Config()
        cfg.index.index_file = json_index_file.name
        cfg.project_root = json_index_file.parent
        idx = FileIndex(cfg)
        idx.load()
        return idx

    def test_load_json_index(self, json_file_index):
        assert json_file_index.total_entries > 0
        assert json_file_index.has_sizes is True

    def test_file_sizes(self, json_file_index):
        node = json_file_index.get_node("MAME/ROMs/pacman.zip")
        assert node is not None
        assert node.size == 1048576

    def test_directory_total_size(self, json_file_index):
        # Use get_dir_size for directory size calculation (lazy loading)
        size = json_file_index.get_dir_size("MAME/ROMs")
        assert size == 1048576 + 2097152

    def test_selection_size(self, json_file_index):
        size = json_file_index.get_selection_size(["MAME/ROMs"])
        assert size == 1048576 + 2097152

    def test_total_size(self, json_file_index):
        total = json_file_index.total_size
        assert total == 1048576 + 2097152 + 512000


class TestFormatSize:
    """Tests for format_size function."""

    def test_bytes(self):
        assert format_size(500) == "500 B"

    def test_kilobytes(self):
        assert format_size(1536) == "1.5 KB"

    def test_megabytes(self):
        assert format_size(1572864) == "1.5 MB"

    def test_gigabytes(self):
        assert format_size(1610612736) == "1.50 GB"

    def test_zero(self):
        assert format_size(0) == "0 B"

    def test_negative(self):
        assert format_size(-1) == "-"
