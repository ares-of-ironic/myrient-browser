"""Download manager with resume, retry, and parallel downloads."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import httpx

from .config import Config
from .state import DownloadItem, DownloadStatus, StateManager

logger = logging.getLogger(__name__)


class DownloadManager:
    """Manages file downloads with resume, retry, and parallel execution."""

    def __init__(
        self,
        config: Config,
        state_manager: StateManager,
        on_progress: Callable[[DownloadItem], None] | None = None,
        on_complete: Callable[[DownloadItem], None] | None = None,
        on_error: Callable[[DownloadItem, str], None] | None = None,
    ):
        self.config = config
        self.state = state_manager
        self.on_progress = on_progress
        self.on_complete = on_complete
        self.on_error = on_error

        self._running = False
        self._semaphore: asyncio.Semaphore | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter: asyncio.Semaphore | None = None
        self._last_request_time: float = 0
        self._queue_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the download manager."""
        if self._running:
            return

        self._running = True
        self._semaphore = asyncio.Semaphore(self.config.download.concurrency)

        if self.config.download.rate_limit > 0:
            self._rate_limiter = asyncio.Semaphore(1)

        # Reset orphaned downloads (stuck in DOWNLOADING from previous run)
        self._reset_orphaned_downloads()

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                self.config.download.timeout,
                connect=10,
                read=60,
            ),
            headers={
                "User-Agent": self.config.server.user_agent,
                "Accept-Encoding": "identity",  # Disable compression for binary files
            },
            follow_redirects=True,
            http2=True,
            limits=httpx.Limits(
                max_connections=self.config.download.concurrency * 2,
                max_keepalive_connections=self.config.download.concurrency,
            ),
        )

        # Start queue processor in background
        self._queue_task = asyncio.create_task(self._process_queue())

    def _reset_orphaned_downloads(self) -> None:
        """Reset downloads stuck in DOWNLOADING status from previous run."""
        orphaned = self.state.get_downloading_items()
        for item in orphaned:
            logger.info(f"Resetting orphaned download: {item.path}")
            self.state.update_item(
                item.path,
                status=DownloadStatus.QUEUED,
            )
        if orphaned:
            self.state.save(force=True)

    async def stop(self) -> None:
        """Stop the download manager."""
        self._running = False

        # Cancel queue processor
        if self._queue_task:
            self._queue_task.cancel()
            try:
                await self._queue_task
            except asyncio.CancelledError:
                pass
            self._queue_task = None

        # Cancel all download tasks
        for task in self._tasks.values():
            task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

        if self._client:
            await self._client.aclose()
            self._client = None

    async def add_to_queue(
        self,
        paths: list[str],
        expanded_from: str = "",
        sizes: dict[str, int] | None = None,
    ) -> int:
        """Add paths to download queue.

        Returns number of items added.
        """
        added = 0
        for path in paths:
            if self.state.get_item(path) is not None:
                continue

            url = self.config.build_url(path)
            local_path = str(self.config.get_local_path(path))
            total_size = (sizes.get(path, 0) if sizes else 0) or 0

            item = DownloadItem(
                path=path,
                url=url,
                local_path=local_path,
                expanded_from=expanded_from,
                total_size=total_size,
            )
            self.state.add_item(item)
            added += 1

        self.state.save()

        if self._running:
            asyncio.create_task(self._process_queue())

        return added

    async def _process_queue(self) -> None:
        """Process queued downloads."""
        while self._running:
            queued = self.state.get_queued_items()
            if not queued:
                await asyncio.sleep(0.5)
                continue

            for item in queued:
                if not self._running:
                    break
                if item.path in self._tasks:
                    continue

                task = asyncio.create_task(self._download_with_semaphore(item))
                self._tasks[item.path] = task

            await asyncio.sleep(0.1)

    async def _download_with_semaphore(self, item: DownloadItem) -> None:
        """Download with concurrency control."""
        try:
            async with self._semaphore:
                await self._download_file(item)
        finally:
            self._tasks.pop(item.path, None)

    async def _download_file(self, item: DownloadItem) -> None:
        """Download a single file with resume and retry support."""
        if not self._client:
            return

        local_path = Path(item.local_path)
        part_path = local_path.with_suffix(local_path.suffix + ".part")

        local_path.parent.mkdir(parents=True, exist_ok=True)

        item.status = DownloadStatus.DOWNLOADING
        item.started_at = time.time()
        self.state.update_item(
            item.path,
            status=DownloadStatus.DOWNLOADING,
            started_at=item.started_at,
        )

        resume_pos = 0
        if part_path.exists():
            resume_pos = part_path.stat().st_size

        retry_count = 0
        max_retries = self.config.download.retries

        while retry_count <= max_retries:
            try:
                await self._do_download(item, part_path, resume_pos)

                part_path.rename(local_path)

                item.status = DownloadStatus.COMPLETED
                item.completed_at = time.time()
                item.progress = 100.0
                self.state.update_item(
                    item.path,
                    status=DownloadStatus.COMPLETED,
                    completed_at=item.completed_at,
                    progress=100.0,
                )
                self.state.save()

                if self.on_complete:
                    self.on_complete(item)

                logger.info(f"Downloaded: {item.path}")
                return

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 416:
                    part_path.unlink(missing_ok=True)
                    resume_pos = 0
                    continue

                error_msg = f"HTTP {e.response.status_code}: {e.response.reason_phrase}"
                retry_count += 1

                if retry_count <= max_retries:
                    delay = min(
                        self.config.download.retry_delay * (2 ** (retry_count - 1)),
                        self.config.download.max_retry_delay,
                    )
                    logger.warning(f"Retry {retry_count}/{max_retries} for {item.path}: {error_msg}")
                    await asyncio.sleep(delay)
                else:
                    await self._handle_failure(item, error_msg)
                    return

            except (httpx.RequestError, OSError) as e:
                error_msg = str(e)
                retry_count += 1

                if retry_count <= max_retries:
                    delay = min(
                        self.config.download.retry_delay * (2 ** (retry_count - 1)),
                        self.config.download.max_retry_delay,
                    )
                    logger.warning(f"Retry {retry_count}/{max_retries} for {item.path}: {error_msg}")
                    await asyncio.sleep(delay)
                else:
                    await self._handle_failure(item, error_msg)
                    return

            except asyncio.CancelledError:
                item.status = DownloadStatus.PAUSED
                self.state.update_item(item.path, status=DownloadStatus.PAUSED)
                self.state.save()
                raise

    async def _do_download(
        self,
        item: DownloadItem,
        part_path: Path,
        resume_pos: int,
    ) -> None:
        """Perform the actual download."""
        if self._rate_limiter:
            async with self._rate_limiter:
                now = time.time()
                min_interval = 1.0 / self.config.download.rate_limit
                elapsed = now - self._last_request_time
                if elapsed < min_interval:
                    await asyncio.sleep(min_interval - elapsed)
                self._last_request_time = time.time()

        headers = {}
        if resume_pos > 0:
            headers["Range"] = f"bytes={resume_pos}-"

        encoded_path = quote(item.path, safe="/")
        url = self.config.build_url(encoded_path)

        async with self._client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()

            content_length = response.headers.get("content-length")
            if content_length:
                total_size = int(content_length) + resume_pos
            else:
                total_size = 0

            item.total_size = total_size
            self.state.update_item(item.path, total_size=total_size)

            mode = "ab" if resume_pos > 0 else "wb"
            downloaded = resume_pos
            start_time = time.time()
            last_update = start_time

            import aiofiles
            async with aiofiles.open(part_path, mode) as f:
                async for chunk in response.aiter_bytes(self.config.download.chunk_size):
                    if not self._running:
                        raise asyncio.CancelledError()

                    await f.write(chunk)
                    downloaded += len(chunk)

                    now = time.time()
                    if now - last_update >= 0.5:
                        elapsed = now - start_time
                        speed = (downloaded - resume_pos) / elapsed if elapsed > 0 else 0

                        if total_size > 0:
                            progress = (downloaded / total_size) * 100
                            remaining = total_size - downloaded
                            eta = remaining / speed if speed > 0 else 0
                        else:
                            progress = 0
                            eta = 0

                        item.downloaded_size = downloaded
                        item.progress = progress
                        item.speed = speed
                        item.eta = eta

                        self.state.update_item(
                            item.path,
                            downloaded_size=downloaded,
                            progress=progress,
                            speed=speed,
                            eta=eta,
                        )

                        if self.on_progress:
                            self.on_progress(item)

                        last_update = now

    async def _handle_failure(self, item: DownloadItem, error: str) -> None:
        """Handle download failure."""
        item.status = DownloadStatus.FAILED
        item.error = error
        self.state.update_item(
            item.path,
            status=DownloadStatus.FAILED,
            error=error,
        )
        self.state.save()

        if self.on_error:
            self.on_error(item, error)

        logger.error(f"Failed to download {item.path}: {error}")

    def get_active_downloads(self) -> list[DownloadItem]:
        """Get currently active downloads."""
        return self.state.get_downloading_items()

    def get_queue_stats(self) -> dict[str, int]:
        """Get queue statistics."""
        return self.state.get_stats()


def check_download_status(config: Config, path: str) -> str:
    """Check if a file has been downloaded.

    Returns:
        "DOWNLOADED" - file exists and is complete
        "PARTIAL" - partial download exists
        "MISSING" - not downloaded
    """
    local_path = config.get_local_path(path)
    part_path = local_path.with_suffix(local_path.suffix + ".part")

    if local_path.exists() and local_path.stat().st_size > 0:
        return "DOWNLOADED"
    if part_path.exists():
        return "PARTIAL"
    return "MISSING"


def get_downloaded_paths(config: Config, paths: list[str]) -> set[str]:
    """Get set of paths that have been downloaded."""
    downloaded = set()
    for path in paths:
        if check_download_status(config, path) == "DOWNLOADED":
            downloaded.add(path)
    return downloaded
