"""Tests for exporter module."""

import json
import tempfile
from pathlib import Path

import pytest

from myrient_browser.config import Config
from myrient_browser.exporter import Exporter, load_selection_file
from myrient_browser.indexer import FileIndex


@pytest.fixture
def sample_index_content():
    return """MAME/
MAME/ROMs/
MAME/ROMs/pacman.zip
MAME/ROMs/galaga.zip
No-Intro/
No-Intro/Nintendo/
No-Intro/Nintendo/game.zip
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
    cfg.index.index_file = index_file.name
    cfg.project_root = index_file.parent
    cfg.server.base_url = "https://example.com/files"
    return cfg


@pytest.fixture
def file_index(config, index_file):
    config.index.index_file = index_file.name
    idx = FileIndex(config)
    idx.load()
    return idx


@pytest.fixture
def exporter(config, file_index):
    return Exporter(config, file_index)


class TestExporter:
    """Tests for Exporter class."""

    def test_export_paths(self, exporter, tmp_path):
        output = tmp_path / "export.txt"
        path, count = exporter.export(
            ["MAME/ROMs/pacman.zip"],
            output_path=output,
            format="paths",
        )

        assert count == 1
        content = output.read_text()
        assert "MAME/ROMs/pacman.zip" in content

    def test_export_urls(self, exporter, tmp_path):
        output = tmp_path / "export.txt"
        path, count = exporter.export(
            ["MAME/ROMs/pacman.zip"],
            output_path=output,
            format="urls",
        )

        content = output.read_text()
        assert "https://example.com/files/MAME/ROMs/pacman.zip" in content

    def test_export_json(self, exporter, tmp_path):
        output = tmp_path / "export.json"
        path, count = exporter.export(
            ["MAME/ROMs/pacman.zip"],
            output_path=output,
            format="json",
        )

        data = json.loads(output.read_text())
        assert len(data) == 1
        assert data[0]["path"] == "MAME/ROMs/pacman.zip"
        assert "url" in data[0]
        assert data[0]["is_dir"] is False

    def test_export_expand_directory(self, exporter, tmp_path):
        output = tmp_path / "export.txt"
        path, count = exporter.export(
            ["MAME/ROMs"],
            output_path=output,
            format="paths",
            expand_dirs=True,
        )

        assert count == 2
        content = output.read_text()
        assert "MAME/ROMs/pacman.zip" in content
        assert "MAME/ROMs/galaga.zip" in content
        assert "MAME/ROMs\n" not in content

    def test_export_no_expand(self, exporter, tmp_path):
        output = tmp_path / "export.txt"
        path, count = exporter.export(
            ["MAME/ROMs"],
            output_path=output,
            format="paths",
            expand_dirs=False,
        )

        assert count == 1
        content = output.read_text()
        assert "MAME/ROMs" in content

    def test_export_json_with_expanded_from(self, exporter, tmp_path):
        output = tmp_path / "export.json"
        path, count = exporter.export(
            ["MAME/ROMs"],
            output_path=output,
            format="json",
            expand_dirs=True,
        )

        data = json.loads(output.read_text())
        assert all(item["expanded_from_dir"] == "MAME/ROMs" for item in data)

    def test_get_export_preview(self, exporter):
        preview, total = exporter.get_export_preview(["MAME/ROMs"], limit=1)
        assert total == 2
        assert len(preview) == 1


class TestLoadSelectionFile:
    """Tests for load_selection_file function."""

    def test_load_text_file(self, tmp_path):
        file = tmp_path / "selection.txt"
        file.write_text("path1\npath2\npath3\n")

        paths = load_selection_file(file)
        assert paths == ["path1", "path2", "path3"]

    def test_load_text_file_with_empty_lines(self, tmp_path):
        file = tmp_path / "selection.txt"
        file.write_text("path1\n\npath2\n  \npath3\n")

        paths = load_selection_file(file)
        assert paths == ["path1", "path2", "path3"]

    def test_load_json_array_of_strings(self, tmp_path):
        file = tmp_path / "selection.json"
        file.write_text('["path1", "path2", "path3"]')

        paths = load_selection_file(file)
        assert paths == ["path1", "path2", "path3"]

    def test_load_json_array_of_objects(self, tmp_path):
        file = tmp_path / "selection.json"
        data = [
            {"path": "path1", "url": "http://example.com/path1"},
            {"path": "path2", "url": "http://example.com/path2"},
        ]
        file.write_text(json.dumps(data))

        paths = load_selection_file(file)
        assert paths == ["path1", "path2"]

    def test_load_nonexistent_file(self, tmp_path):
        file = tmp_path / "nonexistent.txt"
        with pytest.raises(FileNotFoundError):
            load_selection_file(file)
