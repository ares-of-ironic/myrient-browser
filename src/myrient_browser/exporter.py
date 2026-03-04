"""Export functionality for selected paths."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import Config
from .indexer import FileIndex


@dataclass
class ExportItem:
    """Represents an exported item."""

    path: str
    url: str
    is_dir: bool
    expanded_from_dir: str
    local_target: str


ExportFormat = Literal["paths", "urls", "json"]


class Exporter:
    """Handles exporting selected paths to various formats."""

    def __init__(self, config: Config, index: FileIndex):
        self.config = config
        self.index = index

    def export(
        self,
        paths: list[str],
        output_path: Path | None = None,
        format: ExportFormat = "paths",
        expand_dirs: bool = True,
    ) -> tuple[Path, int]:
        """Export selected paths to file.

        Args:
            paths: List of paths to export
            output_path: Output file path (uses default if None)
            format: Export format (paths, urls, json)
            expand_dirs: Whether to expand directories to files

        Returns:
            Tuple of (output_path, count of exported items)
        """
        if output_path is None:
            export_dir = self.config.get_export_dir()
            export_dir.mkdir(parents=True, exist_ok=True)

            if format == "json":
                filename = self.config.export.default_filename.replace(".txt", ".json")
            else:
                filename = self.config.export.default_filename
            output_path = export_dir / filename

        output_path.parent.mkdir(parents=True, exist_ok=True)

        items = self._prepare_items(paths, expand_dirs)

        if format == "paths":
            self._export_paths(items, output_path)
        elif format == "urls":
            self._export_urls(items, output_path)
        elif format == "json":
            self._export_json(items, output_path)

        return output_path, len(items)

    def _prepare_items(self, paths: list[str], expand_dirs: bool) -> list[ExportItem]:
        """Prepare export items from paths."""
        items: list[ExportItem] = []
        seen: set[str] = set()

        if expand_dirs:
            # Use index.expand_selection for efficient expansion
            expanded = self.index.expand_selection(paths)
            
            # Track which directory each file came from
            path_to_source: dict[str, str] = {}
            for path in paths:
                node = self.index.get_node(path)
                if node and node.is_dir:
                    prefix = path + "/"
                    for exp_path in expanded:
                        if exp_path.startswith(prefix) and exp_path not in path_to_source:
                            path_to_source[exp_path] = path
            
            for file_path in expanded:
                if file_path not in seen:
                    seen.add(file_path)
                    items.append(
                        ExportItem(
                            path=file_path,
                            url=self.config.build_url(file_path),
                            is_dir=False,
                            expanded_from_dir=path_to_source.get(file_path, ""),
                            local_target=str(self.config.get_local_path(file_path)),
                        )
                    )
        else:
            for path in paths:
                node = self.index.get_node(path)
                if node is None:
                    continue

                if path not in seen:
                    seen.add(path)
                    items.append(
                        ExportItem(
                            path=path,
                            url=self.config.build_url(path),
                            is_dir=node.is_dir,
                            expanded_from_dir="",
                            local_target=str(self.config.get_local_path(path)),
                        )
                    )

        return items

    def _export_paths(self, items: list[ExportItem], output_path: Path) -> None:
        """Export as plain paths."""
        with open(output_path, "w", encoding="utf-8") as f:
            for item in items:
                f.write(f"{item.path}\n")

    def _export_urls(self, items: list[ExportItem], output_path: Path) -> None:
        """Export as URLs."""
        from urllib.parse import quote

        with open(output_path, "w", encoding="utf-8") as f:
            for item in items:
                encoded_path = quote(item.path, safe="/")
                url = self.config.build_url(encoded_path)
                f.write(f"{url}\n")

    def _export_json(self, items: list[ExportItem], output_path: Path) -> None:
        """Export as JSON."""
        from urllib.parse import quote

        data = []
        for item in items:
            encoded_path = quote(item.path, safe="/")
            data.append({
                "path": item.path,
                "url": self.config.build_url(encoded_path),
                "is_dir": item.is_dir,
                "expanded_from_dir": item.expanded_from_dir,
                "local_target": item.local_target,
            })

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_export_preview(
        self,
        paths: list[str],
        expand_dirs: bool = True,
        limit: int = 10,
    ) -> tuple[list[ExportItem], int]:
        """Get preview of what would be exported.

        Returns tuple of (preview items, total count).
        """
        items = self._prepare_items(paths, expand_dirs)
        return items[:limit], len(items)


def load_selection_file(path: Path) -> list[str]:
    """Load paths from a selection file.

    Supports:
    - Plain text (one path per line)
    - JSON array of paths or objects with 'path' field
    """
    if not path.exists():
        raise FileNotFoundError(f"Selection file not found: {path}")

    content = path.read_text(encoding="utf-8")

    if path.suffix == ".json":
        data = json.loads(content)
        if isinstance(data, list):
            paths = []
            for item in data:
                if isinstance(item, str):
                    paths.append(item)
                elif isinstance(item, dict) and "path" in item:
                    paths.append(item["path"])
            return paths
        raise ValueError("JSON file must contain an array")

    return [line.strip() for line in content.splitlines() if line.strip()]
