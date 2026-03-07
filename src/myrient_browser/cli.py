"""Command-line interface for Myrient Browser."""

from __future__ import annotations

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import click

from . import __version__
from .config import Config
from .downloader import DownloadManager, check_download_status
from .exporter import Exporter, load_selection_file
from .indexer import FileIndex
from .state import StateManager
from .tui import run_tui


def setup_logging(config: Config) -> None:
    """Setup logging configuration."""
    log_path = config.get_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        log_path,
        maxBytes=config.logging.max_log_size,
        backupCount=config.logging.backup_count,
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.logging.log_level.upper()))
    root_logger.addHandler(handler)


def print_disclaimer() -> None:
    """Print usage disclaimer."""
    click.echo(click.style(
        "⚠️  DISCLAIMER: This tool is for downloading content you have legal rights to.",
        fg="yellow",
        bold=True,
    ))
    click.echo(click.style(
        "   Ensure you comply with all applicable laws and terms of service.",
        fg="yellow",
    ))
    click.echo()


@click.group(invoke_without_command=True)
@click.option("--config", "-c", "config_path", type=click.Path(exists=True, path_type=Path),
              help="Path to config file")
@click.option("--version", "-v", is_flag=True, help="Show version")
@click.pass_context
def main(ctx: click.Context, config_path: Path | None, version: bool) -> None:
    """Myrient Browser - Browse and download files from HTTP repository.

    Run without arguments to start interactive TUI.
    """
    if version:
        click.echo(f"myrient-browser {__version__}")
        return

    project_root = Path.cwd()
    config = Config.load(config_path, project_root)

    ctx.ensure_object(dict)
    ctx.obj["config"] = config

    setup_logging(config)

    if ctx.invoked_subcommand is None:
        print_disclaimer()

        try:
            state = StateManager(config)
            state.load()

            # Index will be loaded asynchronously in TUI
            run_tui(config, None, state)
        except FileNotFoundError as e:
            click.echo(click.style(f"Error: {e}", fg="red"), err=True)
            sys.exit(1)
        except KeyboardInterrupt:
            click.echo("\nExiting...")
            sys.exit(0)


@main.command()
@click.argument("query")
@click.option("--limit", "-l", default=50, help="Maximum results")
@click.option("--print-urls", "-u", is_flag=True, help="Print URLs instead of paths")
@click.option("--files-only", "-f", is_flag=True, help="Show only files")
@click.option("--dirs-only", "-d", is_flag=True, help="Show only directories")
@click.pass_context
def search(
    ctx: click.Context,
    query: str,
    limit: int,
    print_urls: bool,
    files_only: bool,
    dirs_only: bool,
) -> None:
    """Search the index for matching paths.

    QUERY can contain | for OR matching (e.g., "c64|commodore").
    """
    config: Config = ctx.obj["config"]

    index = FileIndex(config)
    index.load()

    results = index.search(query, limit=limit, files_only=files_only, dirs_only=dirs_only)

    if not results:
        click.echo("No results found")
        return

    for node in results:
        if print_urls:
            click.echo(config.build_url(node.path))
        else:
            prefix = "📁 " if node.is_dir else "📄 "
            status = check_download_status(config, node.path)
            status_suffix = ""
            if status == "DOWNLOADED":
                status_suffix = click.style(" [DOWNLOADED]", fg="green")
            elif status == "PARTIAL":
                status_suffix = click.style(" [PARTIAL]", fg="yellow")
            click.echo(f"{prefix}{node.path}{status_suffix}")

    click.echo(f"\n{len(results)} results")


@main.command()
@click.argument("query")
@click.option("--out", "-o", "output", type=click.Path(path_type=Path),
              help="Output file path")
@click.option("--urls", is_flag=True, help="Export as URLs")
@click.option("--json", "as_json", is_flag=True, help="Export as JSON")
@click.option("--no-expand", is_flag=True, help="Don't expand directories")
@click.option("--dry-run", is_flag=True, help="Show what would be exported")
@click.pass_context
def export(
    ctx: click.Context,
    query: str,
    output: Path | None,
    urls: bool,
    as_json: bool,
    no_expand: bool,
    dry_run: bool,
) -> None:
    """Search and export matching paths.

    QUERY can contain | for OR matching.
    """
    config: Config = ctx.obj["config"]

    index = FileIndex(config)
    index.load()

    results = index.search(query, limit=10000)

    if not results:
        click.echo("No results found")
        return

    paths = [node.path for node in results]

    exporter = Exporter(config, index)

    format_type = "json" if as_json else ("urls" if urls else "paths")

    if dry_run:
        preview, total = exporter.get_export_preview(paths, expand_dirs=not no_expand, limit=20)
        click.echo(f"Would export {total} items:")
        for item in preview:
            click.echo(f"  {item.path}")
        if total > len(preview):
            click.echo(f"  ... and {total - len(preview)} more")
        return

    output_path, count = exporter.export(
        paths,
        output_path=output,
        format=format_type,
        expand_dirs=not no_expand,
    )

    click.echo(f"Exported {count} items to {output_path}")


@main.command()
@click.option("--from-selection", "-f", "selection_file", type=click.Path(exists=True, path_type=Path),
              help="Load paths from selection file")
@click.option("--paths", "-p", multiple=True, help="Paths to add to queue")
@click.option("--dry-run", is_flag=True, help="Show what would be queued")
@click.pass_context
def queue(
    ctx: click.Context,
    selection_file: Path | None,
    paths: tuple[str, ...],
    dry_run: bool,
) -> None:
    """Add items to download queue.

    Use --from-selection to load from a file, or --paths to specify directly.
    """
    config: Config = ctx.obj["config"]

    all_paths: list[str] = list(paths)

    if selection_file:
        loaded = load_selection_file(selection_file)
        all_paths.extend(loaded)

    if not all_paths:
        click.echo("No paths specified. Use --from-selection or --paths")
        return

    index = FileIndex(config)
    index.load()

    expanded = index.expand_selection(all_paths)

    if dry_run:
        click.echo(f"Would queue {len(expanded)} files:")
        for path in expanded[:20]:
            click.echo(f"  {path}")
        if len(expanded) > 20:
            click.echo(f"  ... and {len(expanded) - 20} more")
        return

    state = StateManager(config)
    state.load()

    async def add_items() -> int:
        downloader = DownloadManager(config, state)
        return await downloader.add_to_queue(expanded)

    added = asyncio.run(add_items())
    state.save_sync()

    click.echo(f"Added {added} files to queue")


@main.command()
@click.option("--all-queued", is_flag=True, help="Download all queued items")
@click.option("--retry-failed", is_flag=True, help="Retry failed downloads")
@click.option("--status", is_flag=True, help="Show queue status")
@click.option("--concurrency", "-c", type=int, default=None, help="Number of concurrent downloads (1-64)")
@click.pass_context
def download(
    ctx: click.Context,
    all_queued: bool,
    retry_failed: bool,
    status: bool,
    concurrency: int | None,
) -> None:
    """Start downloading queued items."""
    config: Config = ctx.obj["config"]
    
    # Override concurrency if specified
    if concurrency is not None:
        concurrency = max(1, min(64, concurrency))
        config.download.concurrency = concurrency

    state = StateManager(config)
    state.load()

    if status:
        stats = state.get_stats()
        click.echo(f"Queue status:")
        click.echo(f"  Queued: {stats['queued']}")
        click.echo(f"  Downloading: {stats['downloading']}")
        click.echo(f"  Completed: {stats['completed']}")
        click.echo(f"  Failed: {stats['failed']}")
        click.echo(f"  Total: {stats['total']}")
        return

    if retry_failed:
        count = state.retry_failed()
        state.save_sync()
        click.echo(f"Reset {count} failed items to queued")

    if all_queued or retry_failed:
        queued = state.get_queued_items()
        if not queued:
            click.echo("No items in queue")
            return

        click.echo(f"Starting download of {len(queued)} items...")
        click.echo(click.style("Press 'q' + Enter to stop gracefully, or Ctrl+C twice to force quit", fg="yellow"))
        print_disclaimer()

        # Flag for graceful shutdown
        shutdown_requested = False
        force_quit = False

        def keyboard_listener():
            """Listen for 'q' key in separate thread."""
            nonlocal shutdown_requested, force_quit
            import select
            import termios
            import tty
            
            # Try to set terminal to raw mode for immediate key detection
            old_settings = None
            try:
                old_settings = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())
            except (termios.error, AttributeError):
                pass  # Not a TTY or Windows
            
            try:
                while not shutdown_requested and not force_quit:
                    # Check if input is available (with timeout)
                    if select.select([sys.stdin], [], [], 0.5)[0]:
                        try:
                            ch = sys.stdin.read(1)
                            if ch.lower() == 'q':
                                shutdown_requested = True
                                click.echo(click.style("\n\n[q] Graceful shutdown requested... waiting for active downloads.", fg="yellow"))
                                break
                        except (IOError, OSError):
                            break
            finally:
                if old_settings:
                    try:
                        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
                    except termios.error:
                        pass

        async def run_downloads() -> None:
            nonlocal shutdown_requested, force_quit
            
            def on_progress(item):
                pass

            def on_complete(item):
                click.echo(click.style(f"✓ {Path(item.path).name}", fg="green"))

            def on_error(item, error):
                click.echo(click.style(f"✗ {Path(item.path).name}: {error}", fg="red"))

            downloader = DownloadManager(
                config, state,
                on_progress=on_progress,
                on_complete=on_complete,
                on_error=on_error,
            )

            # Handle signals gracefully
            import signal
            
            def signal_handler(signum, frame):
                nonlocal shutdown_requested, force_quit
                if shutdown_requested:
                    click.echo("\n\nForce quit - saving state...")
                    force_quit = True
                    state.save_sync()
                    sys.exit(1)
                shutdown_requested = True
                click.echo(click.style("\n\n[Ctrl+C] Graceful shutdown... press again to force quit.", fg="yellow"))
            
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)

            try:
                await downloader.start()

                last_completed = 0
                while state.has_pending and not shutdown_requested:
                    await asyncio.sleep(1)
                    stats = state.get_stats()
                    
                    # Show progress on same line
                    click.echo(
                        f"\rProgress: {stats['completed']} done, "
                        f"{stats['downloading']} active, "
                        f"{stats['queued']} queued, "
                        f"{stats['failed']} failed   ",
                        nl=False,
                    )
                    
                    # Periodic save every 100 completed files
                    if stats['completed'] - last_completed >= 100:
                        state.save(force=True)
                        last_completed = stats['completed']

                if shutdown_requested:
                    click.echo("\n\nStopping downloads...")
                else:
                    click.echo()
            finally:
                await downloader.stop()
                click.echo("\nSaving state...")
                state.save_sync()
                click.echo(click.style("State saved successfully.", fg="green"))

        # Start keyboard listener in background thread
        import threading
        kb_thread = threading.Thread(target=keyboard_listener, daemon=True)
        kb_thread.start()

        try:
            asyncio.run(run_downloads())
        except KeyboardInterrupt:
            shutdown_requested = True
            # Give a moment for graceful shutdown
            click.echo("\nInterrupted - saving state...")
            state.save_sync()

        stats = state.get_stats()
        click.echo(f"\nFinal: {stats['completed']} completed, {stats['failed']} failed")


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show current status of index and queue."""
    config: Config = ctx.obj["config"]

    click.echo(f"Configuration:")
    click.echo(f"  Base URL: {config.server.base_url}")
    click.echo(f"  Download dir: {config.get_download_dir()}")
    click.echo(f"  Concurrency: {config.download.concurrency}")
    click.echo()

    index_path = config.get_index_path()
    if index_path.exists():
        index = FileIndex(config)
        index.load()
        click.echo(f"Index:")
        click.echo(f"  File: {index_path}")
        click.echo(f"  Entries: {index.total_entries:,}")
    else:
        click.echo(click.style(f"Index file not found: {index_path}", fg="red"))

    click.echo()

    state = StateManager(config)
    state.load()
    stats = state.get_stats()
    click.echo(f"Queue:")
    click.echo(f"  Queued: {stats['queued']}")
    click.echo(f"  Downloading: {stats['downloading']}")
    click.echo(f"  Completed: {stats['completed']}")
    click.echo(f"  Failed: {stats['failed']}")


@main.command()
@click.argument("path")
@click.option("--queue-missing", "-q", is_flag=True, help="Add missing files to download queue")
@click.option("--include-mismatch", "-m", is_flag=True, help="Also queue files with size mismatch")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def verify(
    ctx: click.Context,
    path: str,
    queue_missing: bool,
    include_mismatch: bool,
    yes: bool,
) -> None:
    """Verify files on NAS against Myrient index.

    PATH is the Myrient path to verify (e.g., "TOSEC/Commodore/C64/Games").

    Compares files in the Myrient index with files on the configured NAS.
    Shows missing files and optionally adds them to the download queue.

    NAS connection is configured in config.toml under [nas] section.
    """
    from .nas_verify import NASVerifier, format_size
    
    config: Config = ctx.obj["config"]

    click.echo()
    click.echo(click.style("━" * 70, fg="cyan"))
    click.echo(click.style("  NAS Verification", fg="cyan", bold=True))
    click.echo(click.style("━" * 70, fg="cyan"))
    click.echo()

    # Show NAS configuration
    click.echo(f"  NAS Host:     {config.nas.user}@{config.nas.host}:{config.nas.port}")
    click.echo(f"  Remote Path:  {config.nas.remote_path}")
    click.echo(f"  Myrient Path: {path}")
    click.echo(f"  Verify Sizes: {'Yes' if config.nas.verify_sizes else 'No'}")
    click.echo()

    # Load index
    click.echo("Loading index...", nl=False)
    index = FileIndex(config)
    index.load()
    click.echo(click.style(" OK", fg="green"))

    # Create verifier
    verifier = NASVerifier(config, index)

    # Test connection
    click.echo("Testing NAS connection...", nl=False)
    success, message = verifier.test_connection()
    if not success:
        click.echo(click.style(f" FAILED", fg="red"))
        click.echo(click.style(f"  {message}", fg="red"))
        sys.exit(1)
    click.echo(click.style(" OK", fg="green"))

    # Verify
    click.echo("Scanning NAS files...", nl=False)
    result = verifier.verify(path)
    click.echo(click.style(" OK", fg="green"))
    click.echo()

    # Show results
    click.echo(click.style("━" * 70, fg="cyan"))
    click.echo(click.style("  Verification Results", fg="cyan", bold=True))
    click.echo(click.style("━" * 70, fg="cyan"))
    click.echo()

    click.echo(f"  Files in index:      {len(result.index_files):,}")
    click.echo(f"  Files on NAS:        {len(result.nas_files):,}")
    click.echo()

    if result.is_complete:
        click.echo(click.style("  ✓ All files present with correct sizes!", fg="green", bold=True))
        click.echo()
        return

    # Missing files
    if result.missing_files:
        click.echo(click.style(f"  ✗ Missing files:     {len(result.missing_files):,}", fg="red"))
        click.echo(click.style(f"    Total size:        {format_size(result.total_missing_size)}", fg="red"))
    else:
        click.echo(click.style(f"  ✓ Missing files:     0", fg="green"))

    # Size mismatch
    if result.size_mismatch_files:
        click.echo(click.style(f"  ⚠ Size mismatch:     {len(result.size_mismatch_files):,}", fg="yellow"))
        click.echo(click.style(f"    Total size:        {format_size(result.total_mismatch_size)}", fg="yellow"))
    else:
        click.echo(click.style(f"  ✓ Size mismatch:     0", fg="green"))

    # Extra files (info only)
    if result.extra_files:
        click.echo(click.style(f"  ℹ Extra on NAS:      {len(result.extra_files):,}", fg="blue"))

    click.echo()

    # Show sample of missing files
    if result.missing_files:
        click.echo(click.style("  Missing files (first 20):", fg="red"))
        for f in result.missing_files[:20]:
            click.echo(f"    • {f.path} ({format_size(f.size)})")
        if len(result.missing_files) > 20:
            click.echo(f"    ... and {len(result.missing_files) - 20} more")
        click.echo()

    # Show sample of size mismatch files
    if result.size_mismatch_files:
        click.echo(click.style("  Size mismatch files (first 10):", fg="yellow"))
        for idx_file, nas_size in result.size_mismatch_files[:10]:
            click.echo(f"    • {idx_file.path}")
            click.echo(f"      Index: {format_size(idx_file.size)}, NAS: {format_size(nas_size)}")
        if len(result.size_mismatch_files) > 10:
            click.echo(f"    ... and {len(result.size_mismatch_files) - 10} more")
        click.echo()

    # Queue missing files
    if not queue_missing:
        click.echo("Use --queue-missing (-q) to add missing files to download queue.")
        return

    # Calculate what to queue
    files_to_queue: list[str] = []
    total_size = 0

    for f in result.missing_files:
        full_path = f"{path.strip('/')}/{f.path}"
        files_to_queue.append(full_path)
        total_size += f.size

    if include_mismatch:
        for idx_file, _ in result.size_mismatch_files:
            full_path = f"{path.strip('/')}/{idx_file.path}"
            if full_path not in files_to_queue:
                files_to_queue.append(full_path)
                total_size += idx_file.size

    if not files_to_queue:
        click.echo("No files to queue.")
        return

    click.echo(click.style("━" * 70, fg="cyan"))
    click.echo(click.style("  Queue Summary", fg="cyan", bold=True))
    click.echo(click.style("━" * 70, fg="cyan"))
    click.echo()
    click.echo(f"  Files to queue:  {len(files_to_queue):,}")
    click.echo(f"  Total size:      {format_size(total_size)}")
    click.echo()

    # Confirmation
    if not yes:
        if not click.confirm("Add these files to download queue?"):
            click.echo("Cancelled.")
            return

    # Add to queue
    click.echo()
    click.echo("Adding files to queue...", nl=False)

    state = StateManager(config)
    state.load()

    downloader = DownloadManager(config, state)

    async def add_files():
        added, existing = await downloader.add_to_queue(files_to_queue)
        return added, existing

    added, existing = asyncio.run(add_files())
    state.save_sync()

    click.echo(click.style(" OK", fg="green"))
    click.echo()
    click.echo(f"  Added to queue:      {added:,}")
    click.echo(f"  Already in queue:    {existing:,}")
    click.echo()
    click.echo(click.style("Done! Run 'myrient download --all-queued' to start downloading.", fg="green"))


if __name__ == "__main__":
    main()
