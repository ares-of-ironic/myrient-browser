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
    state.save(force=True)

    click.echo(f"Added {added} files to queue")


@main.command()
@click.option("--all-queued", is_flag=True, help="Download all queued items")
@click.option("--retry-failed", is_flag=True, help="Retry failed downloads")
@click.option("--status", is_flag=True, help="Show queue status")
@click.pass_context
def download(
    ctx: click.Context,
    all_queued: bool,
    retry_failed: bool,
    status: bool,
) -> None:
    """Start downloading queued items."""
    config: Config = ctx.obj["config"]

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
        state.save(force=True)
        click.echo(f"Reset {count} failed items to queued")

    if all_queued or retry_failed:
        queued = state.get_queued_items()
        if not queued:
            click.echo("No items in queue")
            return

        click.echo(f"Starting download of {len(queued)} items...")
        print_disclaimer()

        async def run_downloads() -> None:
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

            try:
                await downloader.start()

                while state.has_pending:
                    await asyncio.sleep(1)
                    stats = state.get_stats()
                    click.echo(
                        f"\rProgress: {stats['completed']} done, "
                        f"{stats['downloading']} active, "
                        f"{stats['queued']} queued, "
                        f"{stats['failed']} failed",
                        nl=False,
                    )

                click.echo()
            finally:
                await downloader.stop()
                state.save(force=True)

        try:
            asyncio.run(run_downloads())
        except KeyboardInterrupt:
            click.echo("\nDownload interrupted. Progress saved.")
            state.save(force=True)

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


if __name__ == "__main__":
    main()
