"""Download manager with resume, retry, and parallel downloads."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import httpx

from .config import Config
from .state import DownloadItem, DownloadStatus, StateManager

logger = logging.getLogger(__name__)

# Concurrency limits
CONCURRENCY_MIN = 1
CONCURRENCY_MAX = 32

# Assembly read buffer — 64 MB at a time to avoid RAM spike on huge files
_ASSEMBLE_CHUNK = 64 * 1024 * 1024


@dataclass
class _Seg:
    """State for a single HTTP Range segment."""
    idx: int
    start: int   # first byte (absolute)
    end: int     # last  byte (absolute, inclusive)
    path: Path   # temporary .segN file
    resume: int = 0    # bytes already on disk for this segment
    done: bool = field(default=False, repr=False)

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def _seg_path(part_path: Path, idx: int) -> Path:
    """Return .segN sidecar path next to the .part file."""
    return part_path.with_suffix(f".seg{idx}")

# Backoff settings for 429 / 503
_429_BASE_DELAY = 30.0    # minimum wait on rate-limit response
_429_MAX_DELAY  = 300.0   # cap at 5 min
_JITTER_FACTOR  = 0.25    # ±25 % randomisation on every retry delay


def _add_jitter(delay: float) -> float:
    """Return delay ± JITTER_FACTOR to avoid thundering-herd retries."""
    spread = delay * _JITTER_FACTOR
    return delay + random.uniform(-spread, spread)


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
        # UI indicator only — does NOT block the queue loop.
        # Set when user presses P (pause existing items), cleared on R (resume).
        self._queue_paused = False
        self._semaphore: asyncio.Semaphore | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter: asyncio.Semaphore | None = None
        self._last_request_time: float = 0
        self._queue_task: asyncio.Task | None = None

        # Live concurrency — may differ from config after set_concurrency()
        self._concurrency: int = config.download.concurrency
        # Throttle flag: set when server returns 429/503 to pause new slots
        self._throttled_until: float = 0.0

    # ------------------------------------------------------------------
    # Public: live concurrency control
    # ------------------------------------------------------------------

    @property
    def concurrency(self) -> int:
        """Current active concurrency limit."""
        return self._concurrency

    @property
    def throttle_remaining(self) -> float:
        """Seconds until server-imposed throttle expires (0 if not throttled)."""
        return max(0.0, self._throttled_until - time.time())

    def clear_throttle(self) -> None:
        """Manually cancel the server-imposed throttle and restart the queue loop.

        Use when you know the rate-limit window has passed or you want to retry
        earlier at your own risk.
        """
        self._throttled_until = 0.0
        logger.info("Throttle cleared by user")
        # Kick the queue loop in case it's sleeping
        if self._running and not self._queue_paused:
            asyncio.create_task(self._process_queue())

    @property
    def paused_all(self) -> bool:
        """True when the user has pressed P to pause existing items.

        This is a UI indicator only — the download loop is never blocked.
        New items added while paused_all=True download normally.
        """
        return self._queue_paused

    async def pause_all(self) -> None:
        """Freeze all existing queued/active items without blocking new ones.

        - QUEUED items → PAUSED  (skipped by the queue loop)
        - DOWNLOADING items → cancelled and marked PAUSED
        - Queue loop keeps running; any NEW item added as QUEUED starts immediately.
        - Call resume_all() to move PAUSED items back to QUEUED.
        """
        self._queue_paused = True
        logger.info("Pausing existing queue items")

        # Mark QUEUED items as PAUSED before touching tasks so the queue loop
        # doesn't pick them up again between the status write and task cancel.
        for item in self.state.get_queued_items():
            self.state.update_item(item.path, status=DownloadStatus.PAUSED)

        # Mark DOWNLOADING items PAUSED first (guards _handle_failure race)
        active_paths = list(self._tasks.keys())
        for path in active_paths:
            self.state.update_item(path, status=DownloadStatus.PAUSED)
        # Edge-case: DOWNLOADING items not yet in _tasks
        for item in self.state.get_downloading_items():
            self.state.update_item(item.path, status=DownloadStatus.PAUSED)

        self.state.save(force=True)

        # Cancel in-flight tasks and wait for clean shutdown
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

        logger.info("Queue items paused — download loop still running for new items")

    async def resume_all(self) -> None:
        """Move all PAUSED items back to QUEUED so they resume downloading."""
        self._queue_paused = False
        logger.info("Resuming paused queue items")

        for item in self.state.get_items_by_status(DownloadStatus.PAUSED):
            self.state.update_item(item.path, status=DownloadStatus.QUEUED)
        self.state.save(force=True)

        # Queue loop is already running; it will pick up QUEUED items on next tick
        logger.info("All paused items moved back to QUEUED")

    def set_concurrency(self, value: int) -> int:
        """Change the number of parallel downloads while running.

        The new semaphore takes effect on the next queue cycle; in-flight
        tasks finish normally.  Returns the clamped value that was applied.
        """
        value = max(CONCURRENCY_MIN, min(CONCURRENCY_MAX, value))
        self._concurrency = value
        self._semaphore = asyncio.Semaphore(value)
        logger.info(f"Concurrency changed to {value}")
        return value

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the download manager."""
        if self._running:
            return

        self._running = True
        self._concurrency = self.config.download.concurrency
        self._semaphore = asyncio.Semaphore(self._concurrency)

        if self.config.download.rate_limit > 0:
            self._rate_limiter = asyncio.Semaphore(1)

        # Reset orphaned downloads (stuck in DOWNLOADING from previous run)
        self._reset_orphaned_downloads()

        # Clean up stale .seg* files that could force segmented mode unexpectedly
        self._cleanup_stale_segment_files()

        segs = self.config.download.segments_per_file
        # Each concurrent download may open `segs` parallel segment connections.
        # Add a few extra for HEAD probes and retries.
        max_conn = self._concurrency * (segs + 2) + 4
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                self.config.download.timeout,
                connect=10,
                read=60,
            ),
            headers={
                "User-Agent": self.config.server.user_agent,
                "Accept-Encoding": "identity",  # binary files — no compression
            },
            follow_redirects=True,
            # HTTP/2 uses a 64 KB flow-control window per stream which caps
            # throughput to ~(64 KB / RTT).  HTTP/1.1 lets TCP manage the window
            # (typically 4-16 MB) and matches wget/curl performance.
            http2=False,
            limits=httpx.Limits(
                max_connections=max_conn,
                max_keepalive_connections=self._concurrency * (segs + 1),
            ),
        )

        # Start queue processor in background
        self._queue_task = asyncio.create_task(self._process_queue())

    def _cleanup_stale_segment_files(self) -> None:
        """Remove leftover .seg* files from previous segmented download attempts.

        These files can cause problems when segments_per_file=1 (single-stream mode)
        because the downloader detects them and switches to segmented mode, which
        may trigger server rate-limiting (429) due to parallel requests.
        """
        download_dir = self.config.get_download_dir()
        if not download_dir.exists():
            return

        count = 0
        for seg_file in download_dir.rglob("*.seg[0-9]*"):
            try:
                seg_file.unlink()
                count += 1
            except OSError:
                pass

        if count > 0:
            logger.info(f"Cleaned up {count} stale segment file(s) from previous runs")

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
        force: bool = False,
    ) -> tuple[int, int]:
        """Add paths to download queue.

        Checks whether each file already exists locally; if so it is added with
        status ALREADY_DOWNLOADED and skipped by the downloader unless explicitly
        forced via force_redownload().

        Args:
            force: If True, delete existing files and queue for fresh download.

        Returns:
            (added_new, already_present) – counts of truly new items and items
            that were found on disk (and added as ALREADY_DOWNLOADED).
        """
        added_new = 0
        already_present = 0
        for path in paths:
            existing = self.state.get_item(path)
            if existing is not None:
                # If marked as ALREADY_DOWNLOADED but the file is gone, re-queue it
                if existing.status == DownloadStatus.ALREADY_DOWNLOADED:
                    lp = self.config.get_local_path(path)
                    if not lp.exists():
                        part = lp.with_suffix(lp.suffix + ".part")
                        part.unlink(missing_ok=True)
                        self.state.update_item(
                            path,
                            status=DownloadStatus.QUEUED,
                            progress=0.0,
                            downloaded_size=0,
                            error="",
                            retries=0,
                        )
                        added_new += 1
                        continue
                    # File still on disk — count as already_present
                    already_present += 1
                else:
                    # QUEUED / DOWNLOADING / FAILED / PAUSED / COMPLETED — skip
                    already_present += 1
                continue

            url = self.config.build_url(path)
            lp = self.config.get_local_path(path)
            local_path = str(lp)
            total_size = (sizes.get(path, 0) if sizes else 0) or 0

            # Check whether the file is already on disk
            if lp.exists() and lp.stat().st_size > 0:
                if force:
                    # Force mode: delete existing file and queue for fresh download
                    try:
                        lp.unlink()
                    except OSError:
                        pass
                    item = DownloadItem(
                        path=path,
                        url=url,
                        local_path=local_path,
                        expanded_from=expanded_from,
                        total_size=total_size,
                    )
                    self.state.add_item(item)
                    added_new += 1
                else:
                    local_size = lp.stat().st_size
                    item = DownloadItem(
                        path=path,
                        url=url,
                        local_path=local_path,
                        expanded_from=expanded_from,
                        total_size=total_size or local_size,
                        downloaded_size=local_size,
                        progress=100.0,
                        status=DownloadStatus.ALREADY_DOWNLOADED,
                        local_size=local_size,
                    )
                    self.state.add_item(item)
                    already_present += 1
            else:
                item = DownloadItem(
                    path=path,
                    url=url,
                    local_path=local_path,
                    expanded_from=expanded_from,
                    total_size=total_size,
                )
                self.state.add_item(item)
                added_new += 1

        self.state.save()

        if self._running and added_new > 0:
            asyncio.create_task(self._process_queue())

        return added_new, already_present

    async def force_redownload(self, path: str) -> bool:
        """Force re-download of a file regardless of its current status.

        Resets status to QUEUED and clears progress so the downloader picks it
        up on the next cycle.  Works for ALREADY_DOWNLOADED, COMPLETED, FAILED,
        and PAUSED items.  Returns True if the item was found and reset.
        """
        item = self.state.get_item(path)
        if item is None:
            return False

        # Cancel active download task if any
        task = self._tasks.pop(path, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        # Remove .part file and any .segN sidecar files so we start fresh
        lp = Path(item.local_path)
        part = lp.with_suffix(lp.suffix + ".part")
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        # Glob-remove segment files (.seg0, .seg1, …)
        for sp in lp.parent.glob(f"{lp.name}.seg*"):
            try:
                sp.unlink(missing_ok=True)
            except OSError:
                pass

        self.state.update_item(
            path,
            status=DownloadStatus.QUEUED,
            progress=0.0,
            downloaded_size=0,
            speed=0.0,
            eta=0.0,
            error="",
            retries=0,
            priority=-1,  # put at front of queue
        )
        self.state.save()

        if self._running:
            asyncio.create_task(self._process_queue())

        return True

    async def _process_queue(self) -> None:
        """Process queued downloads using a slot-based approach.

        Only fills available concurrency slots with the highest-priority items.
        Respects throttle windows triggered by 429/503 responses and the
        global pause flag set by pause_all().
        """
        while self._running:
            # Wait out any server-imposed throttle before starting new downloads
            now = time.time()
            if self._throttled_until > now:
                await asyncio.sleep(min(1.0, self._throttled_until - now))
                continue

            active = len(self._tasks)
            available_slots = self._concurrency - active

            if available_slots > 0:
                # get_queued_items() returns items sorted by (priority, added_at)
                # Only fetch as many as we need to fill slots (much faster for large queues)
                queued = self.state.get_queued_items(limit=available_slots + 10)
                started = 0
                for item in queued:
                    if started >= available_slots:
                        break
                    if not self._running:
                        break
                    if item.path in self._tasks:
                        continue
                    task = asyncio.create_task(self._download_with_semaphore(item))
                    self._tasks[item.path] = task
                    started += 1
                # Longer sleep to reduce CPU usage with large queues
                await asyncio.sleep(0.5 if started > 0 else 1.0)
            else:
                await asyncio.sleep(1.0)

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
                status_code = e.response.status_code

                # Range not satisfiable — partial file is corrupt, restart
                if status_code == 416:
                    part_path.unlink(missing_ok=True)
                    resume_pos = 0
                    continue

                error_msg = f"HTTP {status_code}: {e.response.reason_phrase}"
                retry_count += 1

                # 429 Too Many Requests / 503 Service Unavailable:
                # honour Retry-After header and throttle all new downloads
                if status_code in (429, 503):
                    retry_after = e.response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except ValueError:
                            wait = _429_BASE_DELAY
                    else:
                        wait = min(
                            _429_BASE_DELAY * (2 ** (retry_count - 1)),
                            _429_MAX_DELAY,
                        )
                    wait = _add_jitter(wait)
                    self._throttled_until = time.time() + wait
                    logger.warning(
                        f"Rate-limited ({status_code}) — throttling {wait:.1f}s, "
                        f"retry {retry_count}/{max_retries} for {item.path}"
                    )
                    if retry_count <= max_retries:
                        await asyncio.sleep(wait)
                    else:
                        await self._handle_failure(item, error_msg)
                        return
                    continue

                if retry_count <= max_retries:
                    delay = _add_jitter(min(
                        self.config.download.retry_delay * (2 ** (retry_count - 1)),
                        self.config.download.max_retry_delay,
                    ))
                    logger.warning(f"Retry {retry_count}/{max_retries} for {item.path}: {error_msg}")
                    await asyncio.sleep(delay)
                else:
                    await self._handle_failure(item, error_msg)
                    return

            except (httpx.RequestError, OSError) as e:
                error_msg = str(e)
                retry_count += 1

                if retry_count <= max_retries:
                    delay = _add_jitter(min(
                        self.config.download.retry_delay * (2 ** (retry_count - 1)),
                        self.config.download.max_retry_delay,
                    ))
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

    # ------------------------------------------------------------------
    # Download routing — single-stream vs. segmented
    # ------------------------------------------------------------------

    async def _do_download(
        self,
        item: DownloadItem,
        part_path: Path,
        resume_pos: int,
    ) -> None:
        """Route to segmented or single-stream (wget-like) download.

        With segments_per_file=1 (default) this behaves exactly like wget:
        a single GET request that follows redirects automatically and streams
        directly to the .part file. No HEAD probe, no extra round-trips.

        Segmented mode (segments_per_file > 1) requires a HEAD probe to learn
        the total size for Range splitting, then issues N parallel requests.
        """
        encoded_path = quote(item.path, safe="/")
        url = self.config.build_url(encoded_path)

        n_segs = self.config.download.segments_per_file
        min_bytes = int(self.config.download.min_segmented_mb * 1024 * 1024)

        # Check for leftover segment files from a previous segmented attempt
        seg0 = _seg_path(part_path, 0)
        resuming_segs = seg0.exists()

        if n_segs > 1 or resuming_segs:
            # Segmented mode: need a HEAD probe to get total size for range math
            total_size, accepts_ranges, final_url = await self._probe_server(url)
            cdn_url = final_url  # hit CDN directly for all segment requests

            if accepts_ranges and total_size > 0 and (total_size >= min_bytes or resuming_segs):
                if resuming_segs:
                    effective = 0
                    while _seg_path(part_path, effective).exists():
                        effective += 1
                    if effective == 0:
                        effective = n_segs
                else:
                    effective = min(n_segs, max(1, math.ceil(total_size / min_bytes)))

                if effective > 1:
                    await self._do_segmented_download(
                        item, part_path, cdn_url, total_size, effective
                    )
                    return

        # wget-like single-stream: one GET, follow_redirects handles the CDN hop
        await self._do_single_stream(item, part_path, resume_pos, url)

    async def _probe_server(self, url: str) -> tuple[int, bool, str]:
        """HEAD request to discover Content-Length, Range support, and final URL.

        Following redirects once here means every segment/stream request can
        use the final CDN URL directly — no per-request 302 round-trip.

        Returns (content_length, accepts_byte_ranges, final_url).
        Falls back to (0, False, url) on any error.
        """
        try:
            r = await self._client.head(url, timeout=httpx.Timeout(10.0))
            size = int(r.headers.get("content-length", 0))
            accepts = r.headers.get("accept-ranges", "").lower() == "bytes"
            # r.url is the URL of the final response (after redirects)
            final_url = str(r.url)
            return size, accepts, final_url
        except Exception:
            return 0, False, url

    async def _do_segmented_download(
        self,
        item: DownloadItem,
        part_path: Path,
        url: str,
        total_size: int,
        n_segs: int,
    ) -> None:
        """Download a file as N parallel HTTP Range segments, then assemble."""
        seg_size = math.ceil(total_size / n_segs)

        segments: list[_Seg] = []
        for i in range(n_segs):
            start = i * seg_size
            end = min(start + seg_size - 1, total_size - 1)
            sp = _seg_path(part_path, i)
            resume = sp.stat().st_size if sp.exists() else 0
            segments.append(_Seg(i, start, end, sp, resume))

        item.total_size = total_size
        self.state.update_item(item.path, total_size=total_size)

        # Per-segment byte counters — asyncio is single-threaded so no lock
        seg_dl: list[int] = [s.resume for s in segments]

        import aiofiles  # noqa: PLC0415

        async def _download_seg(seg: _Seg) -> None:
            if seg.resume >= seg.length:
                seg.done = True
                return

            range_start = seg.start + seg.resume
            headers = {"Range": f"bytes={range_start}-{seg.end}"}

            # Small stagger so N connections don't all hit the server at once
            await asyncio.sleep(seg.idx * 0.08)

            mode = "ab" if seg.resume > 0 else "wb"
            async with self._client.stream("GET", url, headers=headers) as resp:
                resp.raise_for_status()
                async with aiofiles.open(seg.path, mode) as f:
                    async for chunk in resp.aiter_bytes(self.config.download.chunk_size):
                        if not self._running:
                            raise asyncio.CancelledError()
                        await f.write(chunk)
                        seg_dl[seg.idx] += len(chunk)
            seg.done = True

        # Monitor: aggregates per-segment counters and updates item every 0.5 s
        start_time = time.time()
        initial_dl = sum(seg_dl)

        async def _monitor() -> None:
            prev_dl = initial_dl
            prev_t = start_time
            while not all(s.done for s in segments):
                await asyncio.sleep(0.5)
                total_dl = sum(seg_dl)
                now = time.time()
                dt = now - prev_t
                speed = max(0.0, (total_dl - prev_dl) / dt) if dt > 0 else 0.0
                prev_dl = total_dl
                prev_t = now
                progress = total_dl / total_size * 100
                eta = (total_size - total_dl) / speed if speed > 0 else 0.0
                item.downloaded_size = total_dl
                item.progress = progress
                item.speed = speed
                item.eta = eta
                self.state.update_item(
                    item.path,
                    downloaded_size=total_dl,
                    progress=progress,
                    speed=speed,
                    eta=eta,
                )
                if self.on_progress:
                    self.on_progress(item)

        tasks = [asyncio.create_task(_download_seg(seg)) for seg in segments]
        monitor_task = asyncio.create_task(_monitor())

        try:
            await asyncio.gather(*tasks)
        except Exception:
            monitor_task.cancel()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, monitor_task, return_exceptions=True)
            raise

        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

        # Assemble segments into the .part file then clean up .segN files
        logger.info(
            f"Assembling {n_segs} segments for {item.path} ({total_size:,} bytes)"
        )
        with open(part_path, "wb") as out:
            for seg in segments:
                with open(seg.path, "rb") as inp:
                    shutil.copyfileobj(inp, out, _ASSEMBLE_CHUNK)
                seg.path.unlink(missing_ok=True)

    async def _do_single_stream(
        self,
        item: DownloadItem,
        part_path: Path,
        resume_pos: int,
        url: str | None = None,
    ) -> None:
        """Single-connection streaming download (original implementation)."""
        if self._rate_limiter:
            async with self._rate_limiter:
                now = time.time()
                min_interval = 1.0 / self.config.download.rate_limit
                elapsed = now - self._last_request_time
                if elapsed < min_interval:
                    await asyncio.sleep(min_interval - elapsed)
                self._last_request_time = time.time()

        headers: dict[str, str] = {}
        if resume_pos > 0:
            headers["Range"] = f"bytes={resume_pos}-"

        if url is None:
            encoded_path = quote(item.path, safe="/")
            url = self.config.build_url(encoded_path)

        import aiofiles  # noqa: PLC0415

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
        # Don't overwrite PAUSED — pause_all() sets it before cancelling tasks
        current = self.state.state.items.get(item.path)
        if current and current.status == DownloadStatus.PAUSED:
            return
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
