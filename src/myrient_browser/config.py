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
    user_agent: str = "MyrientBrowser/1.0"


@dataclass
class DownloadConfig:
    """Download configuration."""

    download_dir: str = "downloads"
    concurrency: int = 4
    retries: int = 3
    retry_delay: float = 2.0
    max_retry_delay: float = 60.0
    chunk_size: int = 8388608  # 8 MB — larger chunks reduce syscall overhead
    timeout: int = 60
    rate_limit: float = 0
    segments_per_file: int = 4     # parallel HTTP Range segments per file (1 = off)
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


@dataclass
class DisplayConfig:
    """Display configuration."""

    # Use decimal units (1 KB = 1000 B) instead of binary (1 KB = 1024 B)
    use_decimal_units: bool = True
    # Pass -h to du (human-readable sizes). False = raw block count (faster, exact).
    du_human_readable: bool = False


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
