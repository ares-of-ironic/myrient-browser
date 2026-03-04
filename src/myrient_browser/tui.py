"""Terminal User Interface using Textual."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    ProgressBar,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from .config import Config
from .downloader import DownloadManager, check_download_status
from .exporter import Exporter
from .indexer import FileIndex, IndexNode, format_size as _format_size_base
from .state import DownloadItem, DownloadStatus, StateManager

# Global config reference for format_size
_display_config: Config | None = None


def format_size(size: int) -> str:
    """Format size using global display config."""
    use_decimal = _display_config.display.use_decimal_units if _display_config else True
    return _format_size_base(size, use_decimal=use_decimal)


class PathItem(ListItem):
    """A list item representing a path in the index."""

    def __init__(
        self,
        node: IndexNode,
        selected: bool = False,
        download_status: str = "MISSING",
        size: int = -1,
    ) -> None:
        self.node = node
        self.is_selected = selected
        self.download_status = download_status
        
        icon = "📁 " if node.is_dir else "📄 "
        # Escape square brackets to prevent Rich markup interpretation
        name = node.name.replace("[", "\\[")

        if selected:
            check = "✓ "
        else:
            check = ""

        # Format size
        size_str = ""
        if size >= 0:
            size_str = f" [dim]({format_size(size)})[/]"

        if download_status == "DOWNLOADED":
            label_text = f"{icon}{check}[dim green]{name}[/]{size_str} [green]\\[DOWNLOADED][/]"
        elif download_status == "PARTIAL":
            label_text = f"{icon}{check}[yellow]{name}{size_str} \\[PARTIAL][/]"
        else:
            label_text = f"{icon}{check}{name}{size_str}"

        super().__init__(Label(label_text))


class InfoPanel(Static):
    """Panel showing details about selected item."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.current_node: IndexNode | None = None

    def update_info(self, node: IndexNode | None, config: Config, dir_size: int = -1) -> None:
        """Update panel with node information."""
        self.current_node = node

        if node is None:
            self.update("No item selected")
            return

        status = check_download_status(config, node.path)
        status_style = {
            "DOWNLOADED": "[green]DOWNLOADED[/green]",
            "PARTIAL": "[yellow]PARTIAL[/yellow]",
            "MISSING": "[dim]NOT DOWNLOADED[/dim]",
        }.get(status, status)

        # Escape square brackets in paths to prevent Rich markup interpretation
        escaped_path = node.path.replace("[", "\\[")
        escaped_url = config.build_url(node.path).replace("[", "\\[")
        escaped_local = str(config.get_local_path(node.path)).replace("[", "\\[")
        
        info = f"""[bold]Path:[/bold] {escaped_path}
[bold]Type:[/bold] {"Directory" if node.is_dir else "File"}
[bold]URL:[/bold] {escaped_url}
[bold]Local:[/bold] {escaped_local}
[bold]Status:[/bold] {status_style}"""

        if node.is_dir:
            if dir_size >= 0:
                info += f"\n[bold]Size:[/bold] {format_size(dir_size)}"
        else:
            if node.size >= 0:
                info += f"\n[bold]Size:[/bold] {format_size(node.size)}"

        self.update(info)

    def update_download_info(self, item: DownloadItem | None) -> None:
        """Update panel with download item information."""
        if item is None:
            self.update("No download selected")
            return

        status_style = {
            DownloadStatus.QUEUED: "[dim]QUEUED[/dim]",
            DownloadStatus.DOWNLOADING: "[blue]DOWNLOADING[/blue]",
            DownloadStatus.COMPLETED: "[green]COMPLETED[/green]",
            DownloadStatus.FAILED: "[red]FAILED[/red]",
            DownloadStatus.PAUSED: "[yellow]PAUSED[/yellow]",
        }.get(item.status, str(item.status))

        # Escape square brackets in paths to prevent Rich markup interpretation
        escaped_name = Path(item.path).name.replace("[", "\\[")
        escaped_path = item.path.replace("[", "\\[")
        escaped_url = item.url.replace("[", "\\[")
        escaped_local = item.local_path.replace("[", "\\[")
        
        info = f"""[bold]File:[/bold] {escaped_name}
[bold]Path:[/bold] {escaped_path}
[bold]URL:[/bold] {escaped_url}
[bold]Local:[/bold] {escaped_local}
[bold]Status:[/bold] {status_style}"""

        if item.total_size > 0:
            info += f"\n[bold]Size:[/bold] {format_size(item.total_size)}"
            info += f"\n[bold]Downloaded:[/bold] {format_size(item.downloaded_size)} ({item.progress:.1f}%)"

        if item.error:
            info += f"\n[bold]Error:[/bold] [red]{item.error}[/red]"

        self.update(info)


class LoadingScreen(ModalScreen[None]):
    """Loading screen shown during startup."""

    def compose(self) -> ComposeResult:
        with Container(id="loading-dialog"):
            yield Static("[bold cyan]Myrient Browser[/bold cyan]", id="loading-title")
            yield Static("", id="loading-message")
            yield ProgressBar(id="loading-progress", show_eta=False)

    def update_message(self, message: str) -> None:
        """Update loading message."""
        try:
            self.query_one("#loading-message", Static).update(message)
        except Exception:
            pass

    def set_progress(self, progress: float) -> None:
        """Set progress bar value (0-100)."""
        try:
            self.query_one("#loading-progress", ProgressBar).update(progress=progress)
        except Exception:
            pass


class HelpScreen(ModalScreen[None]):
    """Help screen showing all keyboard shortcuts."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("h", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    def compose(self) -> ComposeResult:
        help_text = """[bold cyan]Myrient Browser - Keyboard Shortcuts[/bold cyan]

[bold]Navigation (Browser tab):[/bold]
  [yellow]Enter[/yellow]      Enter directory
  [yellow]Backspace[/yellow]  Go back / parent directory
  [yellow]g[/yellow]          Go to folder containing highlighted item
  [yellow]/[/yellow]          Focus search input
  [yellow]Escape[/yellow]     Clear search, go to root

[bold]Selection:[/bold]
  [yellow]Space[/yellow]      Toggle select item (recursive for dirs)
  [yellow]a[/yellow]          Select all in current view
  [yellow]c[/yellow]          Clear all selections

[bold]Actions:[/bold]
  [yellow]d[/yellow]          Download selected/highlighted
  [yellow]e[/yellow]          Export selected/highlighted
  [yellow]r[/yellow]          Reload index
  [yellow]m[/yellow]          Toggle "show only missing" filter

[bold]Downloads tab:[/bold]
  [yellow]/[/yellow]          Focus search input
  [yellow]Escape[/yellow]     Clear search and filters
  [yellow]1-5[/yellow]        Filter: All/Queued/Active/Done/Failed
  [yellow]p[/yellow]          Retry/restart selected download
  [yellow]x[/yellow]          Remove selected from queue
  [yellow]f[/yellow]          Retry all failed downloads
  [yellow]k[/yellow]          Clear all completed downloads
  [yellow]X[/yellow]          Clear entire queue (with confirmation)

[bold]General:[/bold]
  [yellow]h[/yellow]          Show this help
  [yellow]q[/yellow]          Quit application

[dim]Press Escape, h or q to close this help[/dim]"""

        with Container(id="help-dialog"):
            yield Static(help_text, id="help-content")

    def action_close(self) -> None:
        self.dismiss(None)


class ConfirmDialog(ModalScreen[bool]):
    """Confirmation dialog."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
    ]

    def __init__(self, message: str, title: str = "Confirm") -> None:
        super().__init__()
        self.message = message
        self.title_text = title

    def compose(self) -> ComposeResult:
        with Container(id="confirm-dialog"):
            yield Label(self.title_text, id="confirm-title")
            yield Static(self.message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes (y)", id="btn-yes", variant="error")
                yield Button("No (n)", id="btn-no", variant="primary")

    @on(Button.Pressed, "#btn-yes")
    def do_confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#btn-no")
    def do_cancel(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ExportDialog(ModalScreen[tuple[str, str] | None]):
    """Dialog for export options."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, default_path: str) -> None:
        super().__init__()
        self.default_path = default_path

    def compose(self) -> ComposeResult:
        with Container(id="export-dialog"):
            yield Label("Export Selection", id="export-title")
            yield Label("Output file:")
            yield Input(value=self.default_path, id="export-path")
            yield Label("Format:")
            with Horizontal(id="format-buttons"):
                yield Button("Paths", id="btn-paths", variant="primary")
                yield Button("URLs", id="btn-urls")
                yield Button("JSON", id="btn-json")
            with Horizontal(id="dialog-buttons"):
                yield Button("Export", id="btn-export", variant="success")
                yield Button("Cancel", id="btn-cancel")

    @on(Button.Pressed, "#btn-paths")
    def select_paths(self) -> None:
        self.query_one("#btn-paths", Button).variant = "primary"
        self.query_one("#btn-urls", Button).variant = "default"
        self.query_one("#btn-json", Button).variant = "default"

    @on(Button.Pressed, "#btn-urls")
    def select_urls(self) -> None:
        self.query_one("#btn-paths", Button).variant = "default"
        self.query_one("#btn-urls", Button).variant = "primary"
        self.query_one("#btn-json", Button).variant = "default"

    @on(Button.Pressed, "#btn-json")
    def select_json(self) -> None:
        self.query_one("#btn-paths", Button).variant = "default"
        self.query_one("#btn-urls", Button).variant = "default"
        self.query_one("#btn-json", Button).variant = "primary"

    @on(Button.Pressed, "#btn-export")
    def do_export(self) -> None:
        path = self.query_one("#export-path", Input).value
        format_type = "paths"
        if self.query_one("#btn-urls", Button).variant == "primary":
            format_type = "urls"
        elif self.query_one("#btn-json", Button).variant == "primary":
            format_type = "json"
        self.dismiss((path, format_type))

    @on(Button.Pressed, "#btn-cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DownloadPanel(Static):
    """Panel showing download progress with management controls."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.items_list: list[DownloadItem] = []
        self.search_query: str = ""
        self.status_filter: str = "all"

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold]Keys:[/bold] [cyan]p[/] Retry  [cyan]x[/] Remove  [cyan]f[/] Retry failed  [cyan]k[/] Clear done  [cyan]X[/] Clear all  [cyan]/[/] Search  [cyan]1-5[/] Filter",
            id="download-help",
        )
        with Horizontal(id="download-filter-row"):
            yield Input(placeholder="Search downloads... (/)", id="download-search")
            yield Static(
                "[1]All [2]Queued [3]Active [4]Done [5]Failed",
                id="download-filter-buttons",
            )
        yield Static("", id="download-summary")
        yield DataTable(id="download-table")

    def on_mount(self) -> None:
        table = self.query_one("#download-table", DataTable)
        table.add_columns("Status", "File", "Size", "Progress", "Speed", "ETA", "Error")
        table.cursor_type = "row"

    def update_downloads(self, items: list[DownloadItem], stats: dict[str, int] | None = None) -> None:
        """Update download list without losing cursor position.
        
        Args:
            items: Items to display in the table (may be limited)
            stats: Full queue statistics from StateManager.get_stats() for accurate summary
        """
        table = self.query_one("#download-table", DataTable)
        summary_widget = self.query_one("#download-summary", Static)
        
        self.items_list = items
        
        # Use provided stats for accurate counts, or calculate from visible items as fallback
        if stats:
            queued_count = stats.get("queued", 0)
            downloading_count = stats.get("downloading", 0)
            completed_count = stats.get("completed", 0)
            failed_count = stats.get("failed", 0)
            total_count = stats.get("total", 0)
        else:
            queued_count = sum(1 for i in items if i.status == DownloadStatus.QUEUED)
            downloading_count = sum(1 for i in items if i.status == DownloadStatus.DOWNLOADING)
            completed_count = sum(1 for i in items if i.status == DownloadStatus.COMPLETED)
            failed_count = sum(1 for i in items if i.status == DownloadStatus.FAILED)
            total_count = len(items)
        
        # Calculate sizes from visible items (approximation for display)
        total_size = 0
        downloaded_size = 0
        for item in items:
            if item.total_size > 0:
                total_size += item.total_size
                downloaded_size += item.downloaded_size
        
        # Update summary
        remaining = total_size - downloaded_size
        summary_parts = []
        if queued_count > 0:
            summary_parts.append(f"[dim]{queued_count} queued[/]")
        if downloading_count > 0:
            summary_parts.append(f"[blue]{downloading_count} downloading[/]")
        if completed_count > 0:
            summary_parts.append(f"[green]{completed_count} done[/]")
        if failed_count > 0:
            summary_parts.append(f"[red]{failed_count} failed[/]")
        
        if total_size > 0:
            summary_parts.append(f"Total: {format_size(total_size)}")
            if remaining > 0:
                summary_parts.append(f"Remaining: {format_size(remaining)}")
        
        # Show filtered/total count
        filtered_count = stats.get("filtered", len(items)) if stats else len(items)
        if filtered_count < total_count:
            if len(items) < filtered_count:
                summary_parts.insert(0, f"[yellow]Showing {len(items)}/{filtered_count} (filtered from {total_count})[/]")
            else:
                summary_parts.insert(0, f"[yellow]Filtered: {filtered_count}/{total_count}[/]")
        elif len(items) < total_count:
            summary_parts.insert(0, f"[yellow]Showing {len(items)}/{total_count}[/]")
        
        summary_widget.update(" | ".join(summary_parts) if summary_parts else "No downloads")
        
        # Build new row data
        new_rows: dict[str, tuple] = {}
        for item in items:
            status_text = {
                DownloadStatus.QUEUED: "[dim]Queued[/dim]",
                DownloadStatus.DOWNLOADING: "[blue]Downloading[/blue]",
                DownloadStatus.COMPLETED: "[green]Done[/green]",
                DownloadStatus.FAILED: "[red]Failed[/red]",
                DownloadStatus.PAUSED: "[yellow]Paused[/yellow]",
            }.get(item.status, str(item.status))

            name = Path(item.path).name.replace("[", "\\[")
            if len(name) > 30:
                name = name[:27] + "..."

            # File size
            if item.total_size > 0:
                size_str = format_size(item.total_size)
            else:
                size_str = "-"

            progress = f"{item.progress:.1f}%"

            if item.speed > 0:
                if item.speed > 1024 * 1024:
                    speed = f"{item.speed / 1024 / 1024:.1f} MB/s"
                elif item.speed > 1024:
                    speed = f"{item.speed / 1024:.1f} KB/s"
                else:
                    speed = f"{item.speed:.0f} B/s"
            else:
                speed = "-"

            if item.eta > 0:
                if item.eta > 3600:
                    eta = f"{item.eta / 3600:.1f}h"
                elif item.eta > 60:
                    eta = f"{item.eta / 60:.1f}m"
                else:
                    eta = f"{item.eta:.0f}s"
            else:
                eta = "-"

            error = item.error[:15] + "..." if len(item.error) > 15 else item.error
            if item.status == DownloadStatus.FAILED:
                error = f"[red]{error}[/red]"

            new_rows[item.path] = (status_text, name, size_str, progress, speed, eta, error)

        # Get current row keys
        current_keys = set(str(k.value) for k in table.rows.keys())
        new_keys = set(new_rows.keys())
        
        # Remove rows that no longer exist
        for key in current_keys - new_keys:
            table.remove_row(key)
        
        # Update existing rows or add new ones
        for idx, item in enumerate(items):
            row_data = new_rows[item.path]
            if item.path in current_keys:
                # Update existing row
                for col_idx, value in enumerate(row_data):
                    table.update_cell(item.path, table.columns[col_idx].key, value)
            else:
                # Add new row
                table.add_row(*row_data, key=item.path)

    def get_selected_item(self) -> DownloadItem | None:
        """Get currently selected download item."""
        table = self.query_one("#download-table", DataTable)
        if table.cursor_row is not None and table.cursor_row < len(self.items_list):
            return self.items_list[table.cursor_row]
        return None


class MyrientBrowser(App):
    """Main TUI application."""

    CSS = """
    Screen {
        layout: horizontal;
    }

    #main-container {
        width: 2fr;
        height: 100%;
    }

    #browser-panel {
        height: 100%;
        border: solid $primary;
    }

    #search-container {
        height: auto;
        padding: 0 1;
    }

    #search-input {
        width: 100%;
    }

    #path-display {
        height: 1;
        padding: 0 1;
        background: $surface;
    }

    #file-list {
        height: 1fr;
        min-height: 10;
    }

    #side-panel {
        width: 1fr;
        height: 100%;
        layout: vertical;
    }

    #info-panel {
        height: auto;
        min-height: 8;
        border: solid $secondary;
        padding: 1;
    }

    #stats-panel {
        height: auto;
        padding: 1;
        border: solid $secondary;
    }

    #download-panel-content {
        height: 1fr;
        border: solid $secondary;
    }

    #download-help {
        height: auto;
        padding: 0 1;
        background: $surface;
        color: $text-muted;
    }

    #download-filter-row {
        height: auto;
        padding: 0 1;
    }

    #download-search {
        width: 1fr;
        margin-right: 1;
    }

    #download-filter-buttons {
        width: auto;
        padding: 0 1;
        color: $text-muted;
    }

    #download-summary {
        height: auto;
        padding: 0 1;
        background: $surface-darken-1;
    }

    #download-table {
        height: 1fr;
    }

    #filter-container {
        height: auto;
        padding: 0 1;
    }

    #loading-dialog {
        width: 50;
        height: auto;
        padding: 2 3;
        background: $surface;
        border: solid $primary;
    }

    #loading-title {
        text-align: center;
        text-style: bold;
        padding-bottom: 1;
    }

    #loading-message {
        text-align: center;
        padding: 1;
        color: $text-muted;
    }

    #loading-progress {
        width: 100%;
        margin-top: 1;
    }

    #help-dialog {
        width: 60;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $primary;
    }

    #help-content {
        padding: 1;
    }

    #confirm-dialog {
        width: 50;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $error;
    }

    #confirm-title {
        text-align: center;
        text-style: bold;
        color: $error;
        padding-bottom: 1;
    }

    #confirm-message {
        padding: 1;
        text-align: center;
    }

    #confirm-buttons {
        height: auto;
        padding-top: 1;
        align: center middle;
    }

    #confirm-buttons Button {
        margin: 0 1;
    }

    #export-dialog {
        width: 60;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $primary;
    }

    #export-title {
        text-align: center;
        text-style: bold;
        padding-bottom: 1;
    }

    #format-buttons {
        height: auto;
        padding: 1 0;
    }

    #format-buttons Button {
        margin-right: 1;
    }

    #dialog-buttons {
        height: auto;
        padding-top: 1;
    }

    #dialog-buttons Button {
        margin-right: 1;
    }

    TabbedContent {
        height: 1fr;
    }

    TabPane {
        height: 1fr;
        padding: 0;
    }

    #tab-browser {
        height: 1fr;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        # Global
        Binding("q", "quit", "Quit"),
        Binding("h", "show_help", "Help"),
        Binding("escape", "clear_or_back", "Clear/Back"),
        # Browser tab
        Binding("/", "focus_search", "Search", show=False),
        Binding("space", "toggle_select", "Select", show=False),
        Binding("a", "select_all", "Select All", show=False),
        Binding("c", "clear_selection", "Clear Sel.", show=False),
        Binding("d", "add_to_queue", "Download", show=False),
        Binding("e", "export", "Export", show=False),
        Binding("r", "reload_index", "Reload", show=False),
        Binding("backspace", "go_back", "Back", show=False),
        Binding("m", "toggle_missing", "Missing", show=False),
        Binding("g", "go_to_parent", "Go to folder", show=False),
        # Downloads tab
        Binding("p", "retry_selected", "Retry", show=False),
        Binding("x", "remove_download", "Remove", show=False),
        Binding("f", "retry_all_failed", "Retry All", show=False),
        Binding("k", "clear_completed", "Clear Done", show=False),
        Binding("X", "clear_all_downloads", "Clear All", show=False),
        Binding("1", "filter_all", "All", show=False),
        Binding("2", "filter_queued", "Queued", show=False),
        Binding("3", "filter_active", "Active", show=False),
        Binding("4", "filter_done", "Done", show=False),
        Binding("5", "filter_failed", "Failed", show=False),
    ]

    download_search_query = reactive("")
    download_status_filter = reactive("all")

    show_only_missing = reactive(False)
    current_path = reactive("")
    search_query = reactive("")
    index_loading = reactive(True)

    def __init__(
        self,
        config: Config,
        index: FileIndex | None,
        state: StateManager,
    ) -> None:
        super().__init__()
        self.config = config
        self.index = index
        self.state = state
        self.exporter: Exporter | None = None
        self.downloader: DownloadManager | None = None

        # Set global config for format_size
        global _display_config
        _display_config = config

        self.selected_paths: set[str] = set()
        self.current_items: list[IndexNode] = []
        self.downloaded_cache: set[str] = set()
        
        if index is not None:
            self.index_loading = False
            self.exporter = Exporter(config, index)

    def _is_browser_tab(self) -> bool:
        """Check if Browser tab is active."""
        try:
            tabs = self.query_one(TabbedContent)
            return tabs.active == "tab-browser"
        except Exception:
            return True

    def _is_downloads_tab(self) -> bool:
        """Check if Downloads tab is active."""
        try:
            tabs = self.query_one(TabbedContent)
            return tabs.active == "tab-downloads"
        except Exception:
            return False

    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal():
            with Vertical(id="main-container"):
                with TabbedContent():
                    with TabPane("Browser", id="tab-browser"):
                        with Container(id="browser-panel"):
                            with Container(id="search-container"):
                                yield Input(
                                    placeholder="Search (fuzzy)... Use | for OR",
                                    id="search-input",
                                )
                            with Horizontal(id="filter-container"):
                                yield Label("Show only missing: ")
                                yield Switch(id="missing-switch")
                            yield Static("", id="path-display")
                            yield ListView(id="file-list")

                    with TabPane("Downloads", id="tab-downloads"):
                        yield DownloadPanel(id="download-panel-content")

            with Vertical(id="side-panel"):
                yield InfoPanel(id="info-panel")
                yield Static("", id="stats-panel")

        yield Footer()

    def on_mount(self) -> None:
        """Initialize on mount."""
        self.title = "Myrient Browser"
        self.sub_title = "Use responsibly - only download content you have rights to"

        # Start downloader immediately (doesn't need index)
        self.start_downloader()
        self.set_interval(1.0, self.update_download_panel)

        # Load index in background if not already loaded
        if self.index is None:
            self.loading_screen = LoadingScreen()
            self.push_screen(self.loading_screen)
            self.load_index_async()
        else:
            self._finish_index_load()

    @work(exclusive=True, thread=True)
    def load_index_async(self) -> None:
        """Load index in background thread."""
        try:
            self.call_from_thread(lambda: self._update_loading("Initializing..."))
            self.index = FileIndex(self.config)
            
            self.call_from_thread(lambda: self._update_loading("Loading index file..."))
            self.index.load()
            
            self.call_from_thread(lambda: self._update_loading("Finalizing..."))
            self.call_from_thread(self._finish_index_load)
        except Exception as e:
            self.call_from_thread(self._dismiss_loading)
            self.call_from_thread(lambda: self.notify(f"Failed to load index: {e}", severity="error"))

    def _update_loading(self, message: str) -> None:
        """Update loading screen message."""
        if hasattr(self, 'loading_screen') and self.loading_screen:
            self.loading_screen.update_message(message)

    def _dismiss_loading(self) -> None:
        """Dismiss loading screen."""
        if hasattr(self, 'loading_screen') and self.loading_screen:
            try:
                self.pop_screen()
            except Exception:
                pass
            self.loading_screen = None

    def _finish_index_load(self) -> None:
        """Called when index loading is complete."""
        self._dismiss_loading()
        self.index_loading = False
        if self.index:
            self.exporter = Exporter(self.config, self.index)
            if self.config.index.watch_enabled:
                self.index.on_reload(self.on_index_reloaded)
                self.index.start_watcher()
        self.refresh_list()
        self.update_stats()
        self.notify(f"Index loaded: {self.index.total_entries:,} entries" if self.index else "Index not available")

    @work(exclusive=True)
    async def start_downloader(self) -> None:
        """Start download manager."""
        self.downloader = DownloadManager(
            self.config,
            self.state,
            on_progress=self.on_download_progress,
            on_complete=self.on_download_complete,
            on_error=self.on_download_error,
        )
        await self.downloader.start()

    def on_download_progress(self, item: DownloadItem) -> None:
        """Handle download progress update."""
        pass

    def on_download_complete(self, item: DownloadItem) -> None:
        """Handle download completion."""
        self.downloaded_cache.add(item.path)
        self.notify(f"Downloaded: {Path(item.path).name}")

    def on_download_error(self, item: DownloadItem, error: str) -> None:
        """Handle download error."""
        self.notify(f"Failed: {Path(item.path).name}", severity="error")

    def on_index_reloaded(self) -> None:
        """Handle index reload."""
        self.call_from_thread(self.refresh_list)
        self.call_from_thread(self.update_stats)
        self.notify("Index reloaded")

    def refresh_list(self, preserve_cursor: bool = False) -> None:
        """Refresh the file list."""
        list_view = self.query_one("#file-list", ListView)
        path_display = self.query_one("#path-display", Static)
        
        # Save current cursor position
        old_index = list_view.index if preserve_cursor else None
        
        list_view.clear()

        # Show loading message if index not ready
        if self.index is None or self.index_loading:
            path_display.update("[yellow]Loading index...[/yellow]")
            self.current_items = []
            return

        if self.search_query:
            nodes = self.index.search(self.search_query, limit=200)
        else:
            nodes = self.index.get_children(self.current_path)

        if self.show_only_missing:
            nodes = [n for n in nodes if check_download_status(self.config, n.path) != "DOWNLOADED"]

        self.current_items = nodes

        for node in nodes:
            is_selected = node.path in self.selected_paths
            status = check_download_status(self.config, node.path)
            # Get size - for dirs use get_dir_size, for files use node.size
            if self.index.has_sizes:
                size = self.index.get_dir_size(node.path) if node.is_dir else node.size
            else:
                size = -1
            item = PathItem(node, selected=is_selected, download_status=status, size=size)
            list_view.append(item)

        # Restore cursor position
        if old_index is not None and nodes:
            new_index = min(old_index, len(nodes) - 1)
            list_view.index = new_index

        if self.search_query:
            path_display.update(f"Search: {self.search_query} ({len(nodes)} results)")
        else:
            path_display.update(f"/{self.current_path}" if self.current_path else "/")

        # Update stats after refreshing list
        self.update_stats()

    def update_stats(self) -> None:
        """Update statistics panel."""
        stats_panel = self.query_one("#stats-panel", Static)

        queue_stats = self.state.get_stats()
        selected_count = len(self.selected_paths)

        # Index info
        if self.index is None or self.index_loading:
            index_info = "[yellow]Loading...[/yellow]"
        else:
            index_info = f"{self.index.total_entries:,} entries"

        # Calculate selected size
        selected_size_str = ""
        if selected_count > 0 and self.index and self.index.has_sizes:
            selected_size = self.index.get_selection_size(list(self.selected_paths))
            if selected_size >= 0:
                selected_size_str = f" ({format_size(selected_size)})"

        # Current view total size (sum of all visible items)
        current_folder_info = ""
        if self.index and self.index.has_sizes and self.current_items:
            view_total = 0
            for node in self.current_items:
                if node.is_dir:
                    dir_size = self.index.get_dir_size(node.path)
                    if dir_size >= 0:
                        view_total += dir_size
                elif node.size >= 0:
                    view_total += node.size
            
            if self.current_path:
                folder_name = self.current_path.split("/")[-1] if "/" in self.current_path else self.current_path
                current_folder_info = f"\n[bold]View total:[/bold] {format_size(view_total)} ({len(self.current_items)} items)"
            else:
                current_folder_info = f"\n[bold]View total:[/bold] {format_size(view_total)} ({len(self.current_items)} items)"

        stats = f"""[bold]Index:[/bold] {index_info}
[bold]Selected:[/bold] {selected_count}{selected_size_str}{current_folder_info}
[bold]Queue:[/bold] {queue_stats['queued']} queued, {queue_stats['downloading']} active
[bold]Done:[/bold] {queue_stats['completed']} completed, {queue_stats['failed']} failed"""

        stats_panel.update(stats)

    def update_download_panel(self) -> None:
        """Update download panel with search and status filtering."""
        try:
            panel = self.query_one("#download-panel-content", DownloadPanel)
            all_items = self.state.get_all_items()
            stats = self.state.get_stats()
            
            # Apply status filter
            status_filter = self.download_status_filter
            if status_filter == "queued":
                items = [i for i in all_items if i.status == DownloadStatus.QUEUED]
            elif status_filter == "active":
                items = [i for i in all_items if i.status == DownloadStatus.DOWNLOADING]
            elif status_filter == "done":
                items = [i for i in all_items if i.status == DownloadStatus.COMPLETED]
            elif status_filter == "failed":
                items = [i for i in all_items if i.status == DownloadStatus.FAILED]
            else:
                items = all_items
            
            # Apply search filter (fuzzy search on path/filename)
            search_query = self.download_search_query.strip().lower()
            if search_query:
                filtered = []
                for item in items:
                    name = Path(item.path).name.lower()
                    path = item.path.lower()
                    if search_query in name or search_query in path:
                        filtered.append(item)
                items = filtered
            
            # Sort: downloading first, then queued, then by added time
            items.sort(key=lambda x: (
                x.status != DownloadStatus.DOWNLOADING,
                x.status != DownloadStatus.QUEUED,
                -x.added_at,
            ))
            
            # Pass filtered count for display, but use full stats for totals
            filtered_stats = stats.copy()
            filtered_stats["filtered"] = len(items)
            
            panel.update_downloads(items[:100], filtered_stats)
            self.update_stats()
        except Exception:
            pass

    @on(Input.Changed, "#search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        """Handle search input change."""
        self.search_query = event.value
        self.refresh_list()

    @on(Switch.Changed, "#missing-switch")
    def on_missing_switch_changed(self, event: Switch.Changed) -> None:
        """Handle missing filter toggle."""
        self.show_only_missing = event.value
        self.refresh_list()

    @on(Input.Changed, "#download-search")
    def on_download_search_changed(self, event: Input.Changed) -> None:
        """Handle download search input change."""
        self.download_search_query = event.value
        self.update_download_panel()

    @on(ListView.Selected, "#file-list")
    def on_item_selected(self, event: ListView.Selected) -> None:
        """Handle item activation (Enter) in list - enter directory."""
        if isinstance(event.item, PathItem):
            node = event.item.node
            if node.is_dir:
                self.search_query = ""
                self.query_one("#search-input", Input).value = ""
                self.current_path = node.path
                self.refresh_list()
            else:
                info_panel = self.query_one("#info-panel", InfoPanel)
                info_panel.update_info(node, self.config)

    @on(ListView.Highlighted, "#file-list")
    def on_item_highlighted(self, event: ListView.Highlighted) -> None:
        """Handle item highlight (cursor move) - update info panel."""
        if isinstance(event.item, PathItem):
            info_panel = self.query_one("#info-panel", InfoPanel)
            node = event.item.node
            dir_size = -1
            if self.index and node.is_dir and self.index.has_sizes:
                dir_size = self.index.get_dir_size(node.path)
            info_panel.update_info(node, self.config, dir_size)

    @on(DataTable.RowHighlighted, "#download-table")
    def on_download_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle download table row highlight - update info panel."""
        info_panel = self.query_one("#info-panel", InfoPanel)
        try:
            panel = self.query_one("#download-panel-content", DownloadPanel)
            if event.cursor_row is not None and event.cursor_row < len(panel.items_list):
                item = panel.items_list[event.cursor_row]
                info_panel.update_download_info(item)
            else:
                info_panel.update_download_info(None)
        except Exception:
            info_panel.update_download_info(None)

    def action_focus_search(self) -> None:
        """Focus search input - works in both tabs."""
        if self._is_browser_tab():
            self.query_one("#search-input", Input).focus()
        elif self._is_downloads_tab():
            self.query_one("#download-search", Input).focus()

    def action_clear_or_back(self) -> None:
        """Clear search or go back - context aware."""
        if self._is_browser_tab():
            self.action_clear_search()
        elif self._is_downloads_tab():
            self.download_search_query = ""
            self.download_status_filter = "all"
            try:
                self.query_one("#download-search", Input).value = ""
            except Exception:
                pass
            self.update_download_panel()

    def action_clear_search(self) -> None:
        """Clear search and go to root."""
        if not self._is_browser_tab():
            return
        self.search_query = ""
        self.current_path = ""
        search_input = self.query_one("#search-input", Input)
        search_input.value = ""
        self.refresh_list()

    def action_toggle_select(self) -> None:
        """Toggle selection of current item."""
        if not self._is_browser_tab():
            return
        list_view = self.query_one("#file-list", ListView)
        if list_view.highlighted_child is None:
            return

        if isinstance(list_view.highlighted_child, PathItem):
            node = list_view.highlighted_child.node

            if node.path in self.selected_paths:
                self._deselect_recursive(node)
            else:
                self._select_recursive(node)

            self.refresh_list(preserve_cursor=True)
            self.update_stats()

    def _select_recursive(self, node: IndexNode) -> None:
        """Select node and all children."""
        for n in node.get_all_nodes():
            self.selected_paths.add(n.path)

    def _deselect_recursive(self, node: IndexNode) -> None:
        """Deselect node and all children."""
        for n in node.get_all_nodes():
            self.selected_paths.discard(n.path)

    def action_select_all(self) -> None:
        """Select all items in current view."""
        if not self._is_browser_tab():
            return
        for node in self.current_items:
            self._select_recursive(node)
        self.refresh_list(preserve_cursor=True)
        self.update_stats()

    def action_clear_selection(self) -> None:
        """Clear all selections."""
        if not self._is_browser_tab():
            return
        self.selected_paths.clear()
        self.refresh_list(preserve_cursor=True)
        self.update_stats()

    def action_go_back(self) -> None:
        """Go to parent directory."""
        if not self._is_browser_tab():
            return
        if self.search_query:
            self.search_query = ""
            self.query_one("#search-input", Input).value = ""
            self.refresh_list()
            return

        if self.current_path:
            parts = self.current_path.split("/")
            self.current_path = "/".join(parts[:-1])
            self.refresh_list()

    def action_go_to_parent(self) -> None:
        """Go to parent folder of highlighted item."""
        if not self._is_browser_tab():
            return
        
        list_view = self.query_one("#file-list", ListView)
        if not list_view.highlighted_child or not isinstance(list_view.highlighted_child, PathItem):
            return
        
        node = list_view.highlighted_child.node
        target_name = node.name
        
        # Get parent path
        if "/" in node.path:
            parent_path = node.path.rsplit("/", 1)[0]
        else:
            parent_path = ""
        
        # Clear search and navigate to parent
        self.search_query = ""
        self.query_one("#search-input", Input).value = ""
        self.current_path = parent_path
        self.refresh_list()
        
        # Find and highlight the target item
        for idx, item in enumerate(self.current_items):
            if item.name == target_name:
                list_view.index = idx
                break

    def action_add_to_queue(self) -> None:
        """Add selected or highlighted item to download queue."""
        if not self._is_browser_tab():
            return
        paths: list[str] = []
        
        if self.selected_paths:
            paths = list(self.selected_paths)
        else:
            list_view = self.query_one("#file-list", ListView)
            if list_view.highlighted_child and isinstance(list_view.highlighted_child, PathItem):
                paths = [list_view.highlighted_child.node.path]
        
        if not paths:
            self.notify("Nothing selected", severity="warning")
            return

        if not self.index:
            self.notify("Index not loaded", severity="warning")
            return

        expanded = self.index.expand_selection(paths)

        if not expanded:
            self.notify("No files to download", severity="warning")
            return

        file_count = len(expanded)
        total_size = self.index.get_selection_size(expanded) if self.index.has_sizes else 0
        
        size_limit = 1024 * 1024 * 1024  # 1 GB
        file_limit = 10
        
        needs_confirmation = file_count > file_limit or total_size > size_limit
        
        if needs_confirmation:
            size_str = format_size(total_size) if total_size > 0 else "unknown size"
            message = f"[bold]You are about to download a large selection:[/bold]\n\n"
            message += f"  • Files: [cyan]{file_count}[/cyan]\n"
            message += f"  • Total size: [cyan]{size_str}[/cyan]\n\n"
            message += "Do you want to continue?"
            
            def handle_confirm(confirmed: bool | None) -> None:
                if confirmed:
                    self.add_to_download_queue(expanded)
            
            self.push_screen(
                ConfirmDialog(message, "Confirm Download"),
                handle_confirm,
            )
        else:
            self.add_to_download_queue(expanded)

    @work(exclusive=True)
    async def add_to_download_queue(self, paths: list[str]) -> None:
        """Add paths to download queue."""
        if self.downloader:
            added = await self.downloader.add_to_queue(paths)
            self.notify(f"Added {added} files to queue")
            self.update_stats()

    def action_export(self) -> None:
        """Show export dialog."""
        if not self._is_browser_tab():
            return
        paths: list[str] = []
        
        if self.selected_paths:
            paths = list(self.selected_paths)
        else:
            list_view = self.query_one("#file-list", ListView)
            if list_view.highlighted_child and isinstance(list_view.highlighted_child, PathItem):
                paths = [list_view.highlighted_child.node.path]
        
        if not paths:
            self.notify("Nothing selected", severity="warning")
            return

        self._export_paths = paths
        default_path = str(self.config.get_export_dir() / self.config.export.default_filename)
        self.push_screen(ExportDialog(default_path), self.handle_export_result)

    def handle_export_result(self, result: tuple[str, str] | None) -> None:
        """Handle export dialog result."""
        if result is None:
            return

        if not self.exporter:
            self.notify("Index not loaded", severity="warning")
            return

        path_str, format_type = result
        paths = getattr(self, '_export_paths', list(self.selected_paths))

        try:
            output_path, count = self.exporter.export(
                paths,
                output_path=Path(path_str),
                format=format_type,
                expand_dirs=True,
            )
            self.notify(f"Exported {count} files to {output_path}")
        except Exception as e:
            self.notify(f"Export failed: {e}", severity="error")

    def action_reload_index(self) -> None:
        """Reload index from file."""
        if not self._is_browser_tab():
            return
        if not self.index:
            self.notify("Index not loaded yet", severity="warning")
            return
        try:
            self.index.load()
            self.refresh_list()
            self.update_stats()
            self.notify("Index reloaded")
        except Exception as e:
            self.notify(f"Reload failed: {e}", severity="error")

    def action_toggle_missing(self) -> None:
        """Toggle show only missing filter."""
        if not self._is_browser_tab():
            return
        switch = self.query_one("#missing-switch", Switch)
        switch.value = not switch.value

    def action_show_help(self) -> None:
        """Show help screen."""
        self.push_screen(HelpScreen())

    def action_retry_all_failed(self) -> None:
        """Retry all failed downloads."""
        if not self._is_downloads_tab():
            return
        count = self.state.retry_failed()
        if count > 0:
            self.state.save(force=True)
            self.notify(f"Retrying {count} failed downloads")
        else:
            self.notify("No failed downloads to retry", severity="warning")

    def action_clear_completed(self) -> None:
        """Clear all completed downloads from queue."""
        if not self._is_downloads_tab():
            return
        count = self.state.clear_completed()
        if count > 0:
            self.state.save(force=True)
            self.notify(f"Cleared {count} completed downloads")
        else:
            self.notify("No completed downloads to clear", severity="warning")

    def action_clear_all_downloads(self) -> None:
        """Clear entire download queue with confirmation."""
        if not self._is_downloads_tab():
            return
        stats = self.state.get_stats()
        total = stats["total"]
        if total == 0:
            self.notify("Download queue is already empty", severity="warning")
            return

        message = f"[bold]Are you sure you want to clear the entire download queue?[/bold]\n\n"
        message += f"This will remove [red]{total}[/red] items:\n"
        message += f"  • Queued: {stats['queued']}\n"
        message += f"  • Downloading: {stats['downloading']}\n"
        message += f"  • Completed: {stats['completed']}\n"
        message += f"  • Failed: {stats['failed']}\n"
        message += f"  • Paused: {stats['paused']}\n\n"
        message += "[dim]This action cannot be undone.[/dim]"

        def handle_confirm(confirmed: bool | None) -> None:
            if confirmed:
                count = self.state.clear_all()
                self.state.save(force=True)
                self.notify(f"Cleared {count} downloads from queue")

        self.push_screen(
            ConfirmDialog(message, "Clear Download Queue"),
            handle_confirm,
        )

    def _set_download_filter(self, filter_name: str) -> None:
        """Set download status filter and update display."""
        if not self._is_downloads_tab():
            return
        self.download_status_filter = filter_name
        self.update_download_panel()
        self._update_filter_display()

    def _update_filter_display(self) -> None:
        """Update filter buttons display to show active filter."""
        try:
            filter_widget = self.query_one("#download-filter-buttons", Static)
            f = self.download_status_filter
            parts = [
                f"[bold cyan][1]All[/]" if f == "all" else "[1]All",
                f"[bold cyan][2]Queued[/]" if f == "queued" else "[2]Queued",
                f"[bold cyan][3]Active[/]" if f == "active" else "[3]Active",
                f"[bold cyan][4]Done[/]" if f == "done" else "[4]Done",
                f"[bold cyan][5]Failed[/]" if f == "failed" else "[5]Failed",
            ]
            filter_widget.update(" ".join(parts))
        except Exception:
            pass

    def action_filter_all(self) -> None:
        """Show all downloads."""
        self._set_download_filter("all")

    def action_filter_queued(self) -> None:
        """Show only queued downloads."""
        self._set_download_filter("queued")

    def action_filter_active(self) -> None:
        """Show only active downloads."""
        self._set_download_filter("active")

    def action_filter_done(self) -> None:
        """Show only completed downloads."""
        self._set_download_filter("done")

    def action_filter_failed(self) -> None:
        """Show only failed downloads."""
        self._set_download_filter("failed")

    def action_remove_download(self) -> None:
        """Remove selected download from queue."""
        if not self._is_downloads_tab():
            return
        try:
            panel = self.query_one("#download-panel-content", DownloadPanel)
            item = panel.get_selected_item()
            if item:
                self.state.remove_item(item.path)
                self.state.save(force=True)
                self.notify(f"Removed: {Path(item.path).name}")
            else:
                self.notify("No download selected", severity="warning")
        except Exception:
            self.notify("No download selected", severity="warning")

    def action_retry_selected(self) -> None:
        """Retry/restart selected download - interrupts if downloading."""
        if not self._is_downloads_tab():
            return
        try:
            panel = self.query_one("#download-panel-content", DownloadPanel)
            item = panel.get_selected_item()
            if item:
                # Delete partial file to force restart
                local_path = Path(item.local_path)
                part_path = local_path.with_suffix(local_path.suffix + ".part")
                part_path.unlink(missing_ok=True)
                
                if item.status == DownloadStatus.DOWNLOADING:
                    # Mark as queued - downloader will pick it up again
                    self.state.update_item(
                        item.path,
                        status=DownloadStatus.QUEUED,
                        progress=0.0,
                        downloaded_size=0,
                        speed=0.0,
                        eta=0.0,
                        error="",
                        retries=0,
                    )
                    self.state.save(force=True)
                    self.notify(f"Restarting: {Path(item.path).name}")
                elif item.status in (DownloadStatus.FAILED, DownloadStatus.PAUSED):
                    self.state.update_item(
                        item.path,
                        status=DownloadStatus.QUEUED,
                        progress=0.0,
                        downloaded_size=0,
                        error="",
                        retries=0,
                    )
                    self.state.save(force=True)
                    self.notify(f"Retrying: {Path(item.path).name}")
                elif item.status == DownloadStatus.COMPLETED:
                    # Re-download completed file
                    if local_path.exists():
                        local_path.unlink()
                    self.state.update_item(
                        item.path,
                        status=DownloadStatus.QUEUED,
                        progress=0.0,
                        downloaded_size=0,
                        error="",
                    )
                    self.state.save(force=True)
                    self.notify(f"Re-downloading: {Path(item.path).name}")
                elif item.status == DownloadStatus.QUEUED:
                    self.notify("Download is already queued", severity="warning")
            else:
                self.notify("No download selected", severity="warning")
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")

    async def action_quit(self) -> None:
        """Quit application."""
        if self.downloader:
            await self.downloader.stop()
        self.state.save(force=True)
        if self.index:
            self.index.stop_watcher()
        self.exit()


def run_tui(config: Config, index: FileIndex | None, state: StateManager) -> None:
    """Run the TUI application.
    
    If index is None, it will be loaded asynchronously after startup.
    """
    app = MyrientBrowser(config, index, state)
    app.run()
