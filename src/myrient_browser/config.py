"""Configuration management for Myrient Browser."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli


@dataclass
class ServerConfig:
    """Server configuration."""

    base_url: str = "https://myrient.erista.me/files"
    user_agent: str = "Wget/1.25.0"


@dataclass
class DownloadConfig:
    """Download configuration."""

    download_dir: str = "downloads"
    concurrency: int = 4
    retries: int = 3
    retry_delay: float = 2.0
    max_retry_delay: float = 60.0
    chunk_size: int = 1048576  # 1 MB chunks — fewer event-loop ticks for large files
    timeout: int = 60
    rate_limit: float = 0
    segments_per_file: int = 1     # 1 = wget-like single stream (set >1 for parallel Range segments)
    min_segmented_mb: float = 8.0  # min file size (MB) to trigger segmented download


@dataclass
class IndexConfig:
    """Index configuration."""

    index_file: str = "directory/all_paths.txt"
    watch_enabled: bool = False
    watch_interval: int = 60
    search_limit: int = 500  # max results returned by fuzzy/rg search in Browser


@dataclass
class ExportConfig:
    """Export configuration."""

    export_dir: str = "exports"
    default_filename: str = "selection.txt"


@dataclass
class LoggingConfig:
    """Logging configuration."""

    log_file: str = "logs/app.log"
    log_level: str = "INFO"
    max_log_size: int = 10485760
    backup_count: int = 3


@dataclass
class StateConfig:
    """State persistence configuration."""

    state_file: str = "state.json"


# Color palette definitions: (name, primary, secondary, accent, success, error, warning)
COLOR_PALETTES: dict[str, tuple[str, str, str, str, str, str]] = {
    "default":    ("#00d7ff", "#0087d7", "#ffaf00", "#00ff00", "#ff0000", "#ffff00"),  # Cyan/Blue
    "neon":       ("#ff00ff", "#00ffff", "#ffff00", "#00ff00", "#ff0055", "#ff8800"),  # Hot pink/Cyan
    "c64":        ("#8888ff", "#aa44ff", "#ffff77", "#77ff77", "#ff7777", "#ffff00"),  # C64 inspired
    "mc":         ("#00ffff", "#ffff00", "#ffffff", "#00ff00", "#ff0000", "#ffff00"),  # Midnight Commander
    "matrix":     ("#00ff00", "#008800", "#00ff00", "#00ff00", "#ff0000", "#88ff00"),  # Matrix green
    "amber":      ("#ffaa00", "#ff8800", "#ffffff", "#ffff00", "#ff4400", "#ffcc00"),  # Amber terminal
    "dracula":    ("#bd93f9", "#ff79c6", "#f1fa8c", "#50fa7b", "#ff5555", "#ffb86c"),  # Dracula theme
    "solarized":  ("#268bd2", "#2aa198", "#b58900", "#859900", "#dc322f", "#cb4b16"),  # Solarized
    "gruvbox":    ("#83a598", "#b8bb26", "#fabd2f", "#b8bb26", "#fb4934", "#fe8019"),  # Gruvbox
    "synthwave":  ("#ff7edb", "#72f1b8", "#fede5d", "#72f1b8", "#fe4450", "#f97e72"),  # Synthwave
}


@dataclass
class DisplayConfig:
    """Display configuration."""

    # Use decimal units (1 KB = 1000 B) instead of binary (1 KB = 1024 B)
    use_decimal_units: bool = True
    # Pass -h to du (human-readable sizes). False = raw block count (faster, exact).
    du_human_readable: bool = False
    # Force MB display for Total/Remaining in Downloads (never show GB)
    force_mb_in_downloads: bool = False
    # Show combined download speed in Downloads summary
    show_total_speed: bool = True
    # Color palette name (see COLOR_PALETTES)
    color_palette: str = "default"


@dataclass
class Config:
    """Main configuration container."""

    server: ServerConfig = field(default_factory=ServerConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    index: IndexConfig = field(default_factory=IndexConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    state: StateConfig = field(default_factory=StateConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    project_root: Path = field(default_factory=Path.cwd)

    @classmethod
    def load(cls, config_path: Path | None = None, project_root: Path | None = None) -> Config:
        """Load configuration from file and environment variables.

        Priority (highest to lowest):
        1. Environment variables (MYRIENT_*)
        2. Config file (config.toml)
        3. Default values
        """
        if project_root is None:
            project_root = Path.cwd()

        config = cls(project_root=project_root)

        if config_path is None:
            config_path = project_root / "config.toml"

        if config_path.exists():
            config._load_from_file(config_path)

        config._load_from_env()

        return config

    def _load_from_file(self, config_path: Path) -> None:
        """Load configuration from TOML file."""
        with open(config_path, "rb") as f:
            data = tomli.load(f)

        if "server" in data:
            self._update_dataclass(self.server, data["server"])
        if "download" in data:
            self._update_dataclass(self.download, data["download"])
        if "index" in data:
            self._update_dataclass(self.index, data["index"])
        if "export" in data:
            self._update_dataclass(self.export, data["export"])
        if "logging" in data:
            self._update_dataclass(self.logging, data["logging"])
        if "state" in data:
            self._update_dataclass(self.state, data["state"])
        if "display" in data:
            self._update_dataclass(self.display, data["display"])

    def _load_from_env(self) -> None:
        """Load configuration from environment variables."""
        env_mappings = {
            "MYRIENT_BASE_URL": (self.server, "base_url"),
            "MYRIENT_USER_AGENT": (self.server, "user_agent"),
            "MYRIENT_DOWNLOAD_DIR": (self.download, "download_dir"),
            "MYRIENT_CONCURRENCY": (self.download, "concurrency", int),
            "MYRIENT_RETRIES": (self.download, "retries", int),
            "MYRIENT_TIMEOUT": (self.download, "timeout", int),
            "MYRIENT_RATE_LIMIT": (self.download, "rate_limit", float),
            "MYRIENT_INDEX_FILE": (self.index, "index_file"),
            "MYRIENT_WATCH_ENABLED": (self.index, "watch_enabled", lambda x: x.lower() == "true"),
            "MYRIENT_WATCH_INTERVAL": (self.index, "watch_interval", int),
            "MYRIENT_EXPORT_DIR": (self.export, "export_dir"),
            "MYRIENT_LOG_LEVEL": (self.logging, "log_level"),
            "MYRIENT_STATE_FILE": (self.state, "state_file"),
        }

        for env_var, mapping in env_mappings.items():
            value = os.environ.get(env_var)
            if value is not None:
                obj = mapping[0]
                attr = mapping[1]
                converter = mapping[2] if len(mapping) > 2 else str
                try:
                    setattr(obj, attr, converter(value))
                except (ValueError, TypeError):
                    pass

    @staticmethod
    def _update_dataclass(obj: Any, data: dict[str, Any]) -> None:
        """Update dataclass fields from dictionary."""
        for key, value in data.items():
            if hasattr(obj, key):
                setattr(obj, key, value)

    def get_index_path(self) -> Path:
        """Get absolute path to index file."""
        return self.project_root / self.index.index_file

    def get_download_dir(self) -> Path:
        """Get absolute path to download directory."""
        return self.project_root / self.download.download_dir

    def get_export_dir(self) -> Path:
        """Get absolute path to export directory."""
        return self.project_root / self.export.export_dir

    def get_log_path(self) -> Path:
        """Get absolute path to log file."""
        return self.project_root / self.logging.log_file

    def get_state_path(self) -> Path:
        """Get absolute path to state file."""
        return self.project_root / self.state.state_file

    def build_url(self, path: str) -> str:
        """Build full URL for a given path."""
        base = self.server.base_url.rstrip("/")
        path = path.lstrip("/")
        return f"{base}/{path}"

    def get_local_path(self, remote_path: str) -> Path:
        """Get local download path for a remote path."""
        normalized = normalize_path(remote_path)
        return self.get_download_dir() / normalized


    def save_to_toml(self, path: Path | None = None) -> None:
        """Write current configuration to a TOML file.

        Creates or overwrites ``config.toml`` (or the supplied path).
        Only sections that contain non-default values are written; the
        full set is always written so the file is self-documenting.
        """
        if path is None:
            path = self.project_root / "config.toml"

        def _str(v: str) -> str:
            escaped = v.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'

        def _bool(v: bool) -> str:
            return "true" if v else "false"

        lines: list[str] = [
            "# Myrient Browser Configuration",
            "# Generated by the Settings panel — edit manually or via the TUI.",
            "",
            "[server]",
            f"base_url = {_str(self.server.base_url)}",
            f"user_agent = {_str(self.server.user_agent)}",
            "",
            "[download]",
            f"download_dir = {_str(self.download.download_dir)}",
            f"concurrency = {self.download.concurrency}",
            f"retries = {self.download.retries}",
            f"retry_delay = {self.download.retry_delay}",
            f"max_retry_delay = {self.download.max_retry_delay}",
            f"chunk_size = {self.download.chunk_size}",
            f"timeout = {self.download.timeout}",
            f"rate_limit = {self.download.rate_limit}",
            f"segments_per_file = {self.download.segments_per_file}",
            f"min_segmented_mb = {self.download.min_segmented_mb}",
            "",
            "[index]",
            f"index_file = {_str(self.index.index_file)}",
            f"watch_enabled = {_bool(self.index.watch_enabled)}",
            f"watch_interval = {self.index.watch_interval}",
            f"search_limit = {self.index.search_limit}",
            "",
            "[export]",
            f"export_dir = {_str(self.export.export_dir)}",
            f"default_filename = {_str(self.export.default_filename)}",
            "",
            "[logging]",
            f"log_file = {_str(self.logging.log_file)}",
            f"log_level = {_str(self.logging.log_level)}",
            f"max_log_size = {self.logging.max_log_size}",
            f"backup_count = {self.logging.backup_count}",
            "",
            "[state]",
            f"state_file = {_str(self.state.state_file)}",
            "",
            "[display]",
            f"use_decimal_units = {_bool(self.display.use_decimal_units)}",
            f"du_human_readable = {_bool(self.display.du_human_readable)}",
            f"force_mb_in_downloads = {_bool(self.display.force_mb_in_downloads)}",
            f"show_total_speed = {_bool(self.display.show_total_speed)}",
            f"color_palette = {_str(self.display.color_palette)}",
            "",
        ]

        path.write_text("\n".join(lines), encoding="utf-8")


def normalize_path(path: str) -> str:
    """Normalize path to prevent path traversal attacks.

    - Removes leading/trailing slashes
    - Normalizes separators
    - Removes .. and . components
    - Validates no escape from base directory
    """
    path = path.replace("\\", "/")
    path = path.strip("/")

    parts = []
    for part in path.split("/"):
        if part == "..":
            continue
        if part == ".":
            continue
        if part:
            parts.append(part)

    return "/".join(parts)


def validate_path(path: str) -> bool:
    """Validate that a path is safe and doesn't contain traversal attempts."""
    if ".." in path:
        return False
    if path.startswith("/"):
        return False
    normalized = normalize_path(path)
    return normalized == path.strip("/").replace("\\", "/")
