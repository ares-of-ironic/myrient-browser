"""Terminal User Interface using Textual."""

from __future__ import annotations

import asyncio
import math
import random
import subprocess
import sys
from itertools import cycle as _icycle
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.events import Key
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    LoadingIndicator,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from .config import Config
from .downloader import CONCURRENCY_MAX, CONCURRENCY_MIN, DownloadManager, check_download_status
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
            yield LoadingIndicator(id="loading-indicator")
            yield Static("Loading index...", id="loading-message")

    def update_message(self, message: str) -> None:
        """Update loading message."""
        try:
            self.query_one("#loading-message", Static).update(message)
        except Exception:
            pass

    def set_progress(self, progress: float) -> None:
        """No-op - progress bar removed, use update_message instead."""
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

[bold]── Browser tab: Navigation ──────────────────[/bold]
  [yellow]Enter[/yellow]       Enter directory
  [yellow]Backspace[/yellow]   Go up one level
  [yellow]g[/yellow]           Go to folder containing highlighted item (useful after search)
  [yellow]/[/yellow]           Focus search input  [dim](300 ms debounce, powered by rg)[/dim]
  [yellow]Escape[/yellow]      Clear search / go to root
  [yellow]][/yellow] [yellow][][/yellow]        Next / previous page  [dim](large directories, 500 items/page)[/dim]

[bold]── Browser tab: Selection ───────────────────[/bold]
  [yellow]Space[/yellow]       Toggle select item  [dim](recursive for directories)[/dim]
  [yellow]a[/yellow]           Select all visible items
  [yellow]c[/yellow]           Clear all selections

[bold]── Browser tab: Actions ─────────────────────[/bold]
  [yellow]d[/yellow]           Add highlighted / selected to download queue
  [yellow]e[/yellow]           Export highlighted / selected to file
  [yellow]m[/yellow]           Toggle "show missing only" filter
  [yellow]r[/yellow]           Reload index file

[bold]── Downloads tab ────────────────────────────[/bold]
  [yellow]/[/yellow]           Focus search input
  [yellow]Escape[/yellow]      Clear search and filters
  [yellow]][/yellow] [yellow][][/yellow]        Next / previous page  [dim](500 items/page)[/dim]
  [yellow]1[/yellow]-[yellow]5[/yellow]         Filter: All / Queued / Active / Done / Failed
  [yellow]p[/yellow]           Retry / restart selected  [dim](jumps to front of queue)[/dim]
  [yellow]u[/yellow]           Move selected queued item to front of queue
  [yellow]F[/yellow]           Force re-download  [dim](works for "On disk" / Done / any status)[/dim]
  [yellow]x[/yellow]           Remove selected from queue
  [yellow]f[/yellow]           Retry all failed downloads
  [yellow]k[/yellow]           Clear all completed downloads
  [yellow]X[/yellow]           Clear entire queue  [dim](confirmation required)[/dim]
  [yellow]+[/yellow] [yellow]-[/yellow]         Increase / decrease concurrent download slots  [dim](1–32)[/dim]
  [yellow]P[/yellow]           [bold]Pause all[/bold] downloads  [dim](keeps queue intact, cancels active transfers)[/dim]
  [yellow]R[/yellow]           [bold]Resume all[/bold] paused downloads
  [yellow]T[/yellow]           [bold]Clear throttle[/bold]  [dim](skip remaining Rate-limited wait — at your own risk)[/dim]

[bold]── Status indicators ────────────────────────[/bold]
  [cyan]On disk[/cyan]     File already exists locally — will not be re-downloaded unless forced
  [blue]Downloading[/blue] Transfer in progress
  [dim]Queued[/dim]      Waiting in queue  ([cyan]↑[/cyan] = high priority)
  [green]Done[/green]        Download completed successfully
  [red]Failed[/red]      Download failed (use [yellow]p[/yellow] or [yellow]F[/yellow] to retry)
  [yellow]Paused[/yellow]      Individually paused item

[bold]── Tab switching ────────────────────────────[/bold]
  [yellow]B[/yellow]           Switch to [underline]B[/underline]rowser tab
  [yellow]L[/yellow]           Switch to Down[underline]l[/underline]oads tab
  [yellow]O[/yellow]           Switch to [underline]O[/underline]ptions tab

[bold]── General ──────────────────────────────────[/bold]
  [yellow]h[/yellow]           Show / close this help
  [yellow]~[/yellow]           Screensaver  [dim](press any key to return)[/dim]
  [yellow]q[/yellow]           Quit

[dim]Press Escape, h or q to close[/dim]"""

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


# ---------------------------------------------------------------------------
# Screensaver
# ---------------------------------------------------------------------------

_SS_QUOTES: list[str] = [
    # gaming classics
    "All your ROMs are belong to us.",
    "It's dangerous to download alone.  Take this.",
    "The princess is in another folder.",
    "Stay a while and listen — your queue awaits.",
    "Do a barrel roll…  (downloads are unaffected)",
    "It's super effective!  4 segments per file!",
    "A wild 429 appeared!  Myrient used Retry-After!",
    "One does not simply cancel 10 000 queued downloads.",
    "You have died of dysentery.  (retry works though)",
    # classic IT
    "Have you tried turning it off and on again?",
    "404: Sleep not found.",
    "It works on my machine.  Ship the machine.",
    "// TODO: add comment explaining this later  (never did)",
    "sudo make me a sandwich",
    # screensaver meta
    "Loading… just kidding, the index is already in RAM.",
    "Press [~] to exit.  Or don't.  I'm a screensaver, not a cop.",
    "This screensaver uses 0.0 watts.  The rest is pure attitude.",
    "Watching the bouncing box… waiting for the corner hit…",
    # download wisdom
    "Downloading the internet…  0.000001% complete.",
    "Your queue is full of stars.",
    "Git Gud at downloading.",
    "Press F to pay respects to failed downloads.",
    "Why does it work?  Nobody knows.",
    "Retro gaming: where saving the world costs 8 bits.",
    "These ROMs were made in a lab; no animals were harmed.",
    "Remember: save early, save often.  (the queue is persistent anyway)",
    # context-aware (used when stats are interesting)
]

# Context-aware quotes shown only when relevant stats are non-zero
_SS_QUOTES_ACTIVE   = [
    "Electrons hard at work.  Please do not disturb.",
    "Bytes incoming.  Stand by.",
    "Maximum effort.  (downloading)",
    "Shh…  we're downloading.",
]
_SS_QUOTES_FAILED   = [
    "Servers gonna serve.",
    "It happens to the best of us.  Press [p].",
    "Failed?  That's just a plot twist.  Retry!",
    "Even the best heroes respawn.",
]
_SS_QUOTES_DONE_BIG = [
    "Now THAT'S a collection!",
    "Achievement unlocked: Hoarder Mode.",
    "Your HDD has been violated.  In a good way.",
    "Future archaeologists will thank you.",
]

_SS_COLORS: list[str] = [
    "bright_cyan", "bright_green", "bright_yellow",
    "bright_magenta", "bright_blue", "bright_red", "bright_white",
]

# Half-width katakana + digits + latin — all exactly 1 terminal column wide
_MATRIX_CHARS = (
    "ｦｧｨｩｪｫｬｭｮｯｰｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ"
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ@#$%&"
)

_BOX_W = 30   # total box width including borders
_BOX_H = 12   # total box height including borders


class _Buf:
    """Minimal 2-D character canvas — each cell is (char, style)."""

    __slots__ = ("w", "h", "_cells")

    def __init__(self, w: int, h: int) -> None:
        self.w = w
        self.h = h
        self._cells: list[list[tuple[str, str]]] = [
            [(" ", "") for _ in range(w)] for _ in range(h)
        ]

    def put(self, x: int, y: int, ch: str, style: str = "") -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self._cells[y][x] = (ch, style)

    def put_str(self, x: int, y: int, s: str, style: str = "") -> None:
        for i, ch in enumerate(s):
            self.put(x + i, y, ch, style)

    def to_text(self) -> Text:
        t = Text(no_wrap=True, overflow="crop")
        for ri, row in enumerate(self._cells):
            prev_style: str | None = None
            run: list[str] = []
            for ch, style in row:
                if style != prev_style:
                    if run:
                        t.append("".join(run), style=prev_style or "")
                        run = []
                    prev_style = style
                run.append(ch)
            if run:
                t.append("".join(run), style=prev_style or "")
            if ri < len(self._cells) - 1:
                t.append("\n")
        return t


class _ScreensaverWidget(Widget):
    """Matrix rain background + bouncing stats box."""

    DEFAULT_CSS = """
    _ScreensaverWidget {
        background: #000000;
        width: 1fr;
        height: 1fr;
    }
    """

    def __init__(self, state: StateManager, du_result: str = "") -> None:
        super().__init__()
        self._state = state
        self._du_result = du_result   # latest cached du output (updated by app)
        self._tick = 0

        # Bouncing box — slower speed than before
        self._bx = 4.0
        self._by = 2.0
        self._bdx = 0.15   # ~3× slower than original 0.38
        self._bdy = 0.08   # ~2.5× slower than original 0.20
        self._color_iter = _icycle(_SS_COLORS)
        self._color = next(self._color_iter)
        self._corner_flash = 0
        self._corner_count = 0

        # Quote + typing animation
        self._quote = random.choice(_SS_QUOTES)
        self._quote_tick = 0
        self._typed_len = 0

        # Matrix rain: list of column state dicts
        # {x, head_y, speed, chars: dict[int,str], tail_len}
        self._matrix: list[dict] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        w = self.size.width or 80
        h = self.size.height or 24
        self._init_matrix(w, h)
        self.set_interval(1 / 20, self._step)

    def _init_matrix(self, w: int, h: int) -> None:
        """Create one rain column every 2 terminal columns."""
        self._matrix = []
        for x in range(0, w, 2):
            tail = random.randint(5, max(6, h // 2))
            self._matrix.append({
                "x": x,
                "head_y": random.uniform(-h, 0),   # stagger starts
                "speed": random.uniform(0.25, 0.75),
                "chars": {},
                "tail_len": tail,
            })

    # ------------------------------------------------------------------
    # Animation step
    # ------------------------------------------------------------------

    def _pick_quote(self, stats: dict) -> str:
        active = stats.get("downloading", 0)
        failed = stats.get("failed", 0)
        done   = stats.get("completed", 0)
        pool   = list(_SS_QUOTES)
        if active > 0:
            pool += _SS_QUOTES_ACTIVE * 3
        if failed > 0:
            pool += _SS_QUOTES_FAILED * 2
        if done >= 100:
            pool += _SS_QUOTES_DONE_BIG * 2
        return random.choice(pool)

    def _step(self) -> None:
        self._tick += 1
        w, h = self.size.width, self.size.height
        if w == 0:
            return

        # Re-init matrix if terminal resized significantly
        if self._matrix and abs(self._matrix[-1]["x"] - (w - 2)) > 4:
            self._init_matrix(w, h)

        # Advance matrix rain columns
        for col in self._matrix:
            col["head_y"] += col["speed"]
            hy = int(col["head_y"])
            # Assign / randomise head character
            if 0 <= hy < h:
                col["chars"][hy] = random.choice(_MATRIX_CHARS)
            # Randomly mutate a tail character
            if col["chars"] and random.random() < 0.08:
                y = random.choice(list(col["chars"].keys()))
                col["chars"][y] = random.choice(_MATRIX_CHARS)
            # Reset when fully off-screen
            if col["head_y"] > h + col["tail_len"]:
                col["head_y"] = random.uniform(-h * 0.5, 0)
                col["chars"] = {}
                col["tail_len"] = random.randint(5, max(6, h // 2))
                col["speed"] = random.uniform(0.25, 0.75)

        # Bounce box
        max_bx = float(max(0, w - _BOX_W - 1))
        max_by = float(max(0, h - _BOX_H - 3))
        self._bx = min(max(self._bx + self._bdx, 0.0), max_bx)
        self._by = min(max(self._by + self._bdy, 0.0), max_by)

        hit_x = self._bx <= 0.0 or self._bx >= max_bx
        hit_y = self._by <= 0.0 or self._by >= max_by
        if hit_x:
            self._bdx = -self._bdx
        if hit_y:
            self._bdy = -self._bdy
        if hit_x or hit_y:
            self._color = next(self._color_iter)
        if hit_x and hit_y:
            self._corner_count += 1
            self._corner_flash = 50
        if self._corner_flash > 0:
            self._corner_flash -= 1

        # Typing animation — 1 char per 2 ticks ≈ 10 chars/s (readable pace)
        self._typed_len = min(len(self._quote), self._typed_len + 1)

        # New quote every ~20 s
        if self._tick - self._quote_tick >= 400:
            stats = self._state.get_stats()
            self._quote = self._pick_quote(stats)
            self._quote_tick = self._tick
            self._typed_len = 0

        # Sync du result from app if available
        try:
            self._du_result = self.app._du_result  # type: ignore[attr-defined]
        except AttributeError:
            pass

        self.refresh()

    # ------------------------------------------------------------------
    # Box content
    # ------------------------------------------------------------------

    def _build_box(self, stats: dict, downloading: list) -> list[str]:
        inner = _BOX_W - 2   # 28 printable chars
        pad = lambda s: "│" + s[:inner].ljust(inner) + "│"  # noqa: E731

        queued  = stats.get("queued", 0)
        active  = stats.get("downloading", 0)
        done    = stats.get("completed", 0)
        failed  = stats.get("failed", 0)
        on_disk = stats.get("already_downloaded", 0)
        total   = stats.get("total", 0)

        lines: list[str] = [
            "╭" + "─" * inner + "╮",
            pad("  ↓  M Y R I E N T"),
            "│" + "─" * inner + "│",
        ]

        # Active download: progress bar + filename + speed
        if active > 0 and downloading:
            item = downloading[0]
            prog = max(0.0, min(100.0, item.progress))
            bar_w = inner - 12          # "  >> [" + "] 100%  " = 12
            filled = int(prog / 100 * bar_w)
            bar = "█" * filled + "░" * (bar_w - filled)
            lines.append(pad(f"  >> [{bar}] {prog:3.0f}%"))
            name = Path(item.path).name
            if len(name) > inner - 4:
                name = name[:inner - 7] + "..."
            spd = item.speed
            if   spd >= 1_048_576: spd_s = f"{spd/1_048_576:.1f} MB/s"  # noqa: E701
            elif spd >= 1024:      spd_s = f"{spd/1024:.0f} KB/s"        # noqa: E701
            elif spd > 0:          spd_s = f"{spd:.0f} B/s"              # noqa: E701
            else:                  spd_s = "connecting..."               # noqa: E701
            lines.append(pad(f"  {name}"))
            lines.append(pad(f"  {active} active  {spd_s}"))
        else:
            lines.append(pad(f"  downloading  {active:>6}"))

        lines += [
            pad(f"  queued       {queued:>6}"),
            pad(f"  done         {done:>6}"),
            pad(f"  failed       {failed:>6}"),
            pad(f"  on disk      {on_disk:>6}"),
            pad(f"  total        {total:>6}"),
            "│" + "─" * inner + "│",
            pad(f"  disk used  {self._du_result or 'calculating...'}"),
            "╰" + "─" * inner + "╯",
        ]
        return lines

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def render(self) -> Text:
        w, h = self.size.width, self.size.height
        if w < 10 or h < 6:
            return Text()

        buf = _Buf(w, h)

        # ── Matrix rain ──────────────────────────────────────────────
        for col in self._matrix:
            x  = col["x"]
            hy = int(col["head_y"])
            tl = col["tail_len"]
            for y, ch in col["chars"].items():
                dy = hy - y        # 0 = head; positive = tail above head
                if dy < 0 or dy > tl:
                    continue
                if dy == 0:
                    style = "bold bright_white"
                elif dy <= 2:
                    style = "bold #00ff41"   # classic Matrix green
                elif dy <= 6:
                    style = "#00cc33"
                elif dy <= 12:
                    style = "#007722"
                else:
                    style = "dim #003311"
                buf.put(x, y, ch, style)

        # ── Stats box ────────────────────────────────────────────────
        stats       = self._state.get_stats()
        downloading = self._state.get_downloading_items()
        bx, by      = int(self._bx), int(self._by)
        box_style   = f"bold {self._color}"
        for i, line in enumerate(self._build_box(stats, downloading)):
            avail = w - bx
            if avail > 0:
                buf.put_str(bx, by + i, line[:avail], box_style)

        # ── Corner flash ─────────────────────────────────────────────
        if self._corner_flash > 0:
            msgs = [
                " ★  CORNER!  ★ ",
                " ★★  CORNER x2!  ★★ ",
                " ★★★  HAT TRICK!  ★★★ ",
            ]
            msg = msgs[min(self._corner_count - 1, len(msgs) - 1)]
            flash_style = (
                "bold bright_yellow on black"
                if self._corner_flash % 4 < 2
                else "bold black on bright_yellow"
            )
            cx = bx + (_BOX_W - len(msg)) // 2
            buf.put_str(max(0, cx), by + _BOX_H // 2, msg[:w], flash_style)

        # ── Pulse ring ───────────────────────────────────────────────
        if int(abs(math.sin(self._tick / 20)) * 3) == 0:
            rs = f"dim {self._color}"
            rx1, ry1 = bx - 1, by - 1
            rx2, ry2 = bx + _BOX_W, by + _BOX_H
            for rx in range(rx1, rx2 + 1):
                buf.put(rx, ry1, "·", rs)
                buf.put(rx, ry2, "·", rs)
            for ry in range(ry1, ry2 + 1):
                buf.put(rx1, ry, "·", rs)
                buf.put(rx2, ry, "·", rs)

        # ── Quote (typing effect) ────────────────────────────────────
        if h > 4:
            revealed = self._quote[:self._typed_len]
            cursor   = "_" if (self._tick % 10) < 5 else " "
            display  = revealed if self._typed_len >= len(self._quote) else revealed + cursor
            qx = max(0, (w - len(self._quote)) // 2)
            buf.put_str(qx, h - 3, display[:w], "italic #4a7a4a")

        # ── Bottom hint ──────────────────────────────────────────────
        hint = "any key to exit  ·  ~ to return"
        buf.put_str(max(0, (w - len(hint)) // 2), h - 1, hint[:w], "dim #1a3a1a")

        return buf.to_text()


class ScreensaverScreen(ModalScreen[None]):
    """Full-screen animated screensaver (press any key to dismiss)."""

    DEFAULT_CSS = """
    ScreensaverScreen {
        background: #000000;
        padding: 0;
        margin: 0;
    }
    ScreensaverScreen > _ScreensaverWidget {
        width: 100%;
        height: 100%;
    }
    """

    def __init__(self, state: StateManager) -> None:
        super().__init__()
        self._state = state

    def compose(self) -> ComposeResult:
        yield _ScreensaverWidget(self._state)

    def on_key(self, event: Key) -> None:
        event.prevent_default()
        self.dismiss()


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
            "[bold]Keys:[/bold] [cyan]p[/] Retry  [cyan]u[/] Front  [cyan]F[/] Force  [cyan]x[/] Remove  [cyan]f[/] Retry failed  [cyan]k[/] Clear done  [cyan]X[/] Clear all  [cyan]+[/][cyan]-[/] Slots  [bold yellow]P[/] Pause all  [bold green]R[/] Resume  [bold magenta]T[/] Clear throttle  [cyan]/[/] Search  [cyan]1-5[/] Filter",
            id="download-help",
        )
        with Horizontal(id="download-filter-row"):
            yield Input(placeholder="Search downloads... (/)", id="download-search")
            yield Static(
                "[1]All [2]Queued/Paused [3]Active [4]Done [5]Failed",
                id="download-filter-buttons",
            )
        yield Static("", id="download-summary")
        yield Static("", id="download-concurrency")
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
            queued_count  = stats.get("queued", 0)
            paused_count  = stats.get("paused", 0)
            downloading_count = stats.get("downloading", 0)
            completed_count = stats.get("completed", 0)
            failed_count  = stats.get("failed", 0)
            total_count   = stats.get("total", 0)
        else:
            queued_count  = sum(1 for i in items if i.status == DownloadStatus.QUEUED)
            paused_count  = sum(1 for i in items if i.status == DownloadStatus.PAUSED)
            downloading_count = sum(1 for i in items if i.status == DownloadStatus.DOWNLOADING)
            completed_count = sum(1 for i in items if i.status == DownloadStatus.COMPLETED)
            failed_count  = sum(1 for i in items if i.status == DownloadStatus.FAILED)
            total_count   = len(items)
        
        # Use pre-computed sizes from the full filtered list when available
        # (passed via stats["all_total_size"]), so that Total/Remaining reflect
        # ALL matching items, not just the current page.
        if stats and "all_total_size" in stats:
            total_size = stats["all_total_size"]
            downloaded_size = stats["all_downloaded_size"]
        else:
            total_size = sum(i.total_size for i in items if i.total_size > 0)
            downloaded_size = sum(i.downloaded_size for i in items)
        
        # Update summary
        remaining = total_size - downloaded_size
        summary_parts = []
        if queued_count > 0:
            summary_parts.append(f"[dim]{queued_count} queued[/]")
        if paused_count > 0:
            summary_parts.append(f"[yellow]{paused_count} paused[/]")
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
        
        # Show page / filtered / total info
        filtered_count = stats.get("filtered", len(items)) if stats else len(items)
        page = (stats.get("page", 0) if stats else 0) + 1
        max_page = (stats.get("max_page", 0) if stats else 0) + 1
        page_start = stats.get("page_start", 0) + 1 if stats else 1
        page_end = stats.get("page_end", len(items)) if stats else len(items)

        if filtered_count < total_count:
            page_info = f"[yellow]Filtered {filtered_count}/{total_count}[/]"
        else:
            page_info = None

        if filtered_count > len(items):
            pag_info = (
                f"[dim]{page_start}-{page_end}/{filtered_count}, "
                f"str. {page}/{max_page}  "
                f"[cyan]][/cyan] nast.  [cyan]\\[[/cyan] poprz.[/dim]"
            )
            if page_info:
                summary_parts.insert(0, pag_info)
                summary_parts.insert(0, page_info)
            else:
                summary_parts.insert(0, pag_info)
        elif page_info:
            summary_parts.insert(0, page_info)
        
        summary_widget.update(" | ".join(summary_parts) if summary_parts else "No downloads")
        
        # Build new row data
        new_rows: dict[str, tuple] = {}
        for item in items:
            priority_mark = "[cyan]↑[/cyan] " if item.priority < 0 else ""
            status_text = {
                DownloadStatus.QUEUED: f"{priority_mark}[dim]Queued[/dim]",
                DownloadStatus.DOWNLOADING: f"[blue]Downloading[/blue]",
                DownloadStatus.COMPLETED: "[green]Done[/green]",
                DownloadStatus.FAILED: "[red]Failed[/red]",
                DownloadStatus.PAUSED: "[yellow]Paused[/yellow]",
                DownloadStatus.ALREADY_DOWNLOADED: "[cyan]On disk[/cyan]",
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

    def update_concurrency(
        self,
        concurrency: int,
        throttle_remaining: float = 0.0,
        paused_all: bool = False,
    ) -> None:
        """Update the concurrency / pause indicator line."""
        widget = self.query_one("#download-concurrency", Static)
        if paused_all:
            slots_text = (
                "[bold yellow] ⏸  Queue paused[/bold yellow]"
                "  [dim](existing items frozen — new items still download freely)[/dim]"
                "  [dim]Press [bold]R[/bold] to resume all[/dim]"
            )
        else:
            bar = "█" * concurrency + "░" * max(0, 16 - concurrency)
            slots_text = (
                f"[bold]Slots:[/bold] [cyan]{bar}[/cyan] [cyan bold]{concurrency}[/cyan bold]"
                f"  [dim]([cyan]-[/cyan] / [cyan]+[/cyan] to change, max {CONCURRENCY_MAX}"
                f"  [bold]P[/bold] pause all)[/dim]"
            )
            if throttle_remaining > 0:
                slots_text += (
                    f"  [yellow bold]⏸ Rate-limited {throttle_remaining:.0f}s[/yellow bold]"
                    f"  [dim]([bold magenta]T[/bold magenta] to skip)[/dim]"
                )
        widget.update(slots_text)

    def get_selected_item(self) -> DownloadItem | None:
        """Get currently selected download item."""
        table = self.query_one("#download-table", DataTable)
        if table.cursor_row is not None and table.cursor_row < len(self.items_list):
            return self.items_list[table.cursor_row]
        return None


# ---------------------------------------------------------------------------
# Settings tab
# ---------------------------------------------------------------------------

class SettingsPanel(Widget):
    """Settings form — renders all config fields grouped by section.

    Layout: scrollable list of (label | input/switch) rows.
    Changes are applied to the in-memory Config object and written to
    config.toml when the user presses Save (S) or the Save button.
    """

    # Emitted when the user saves settings
    class Saved(Message):
        pass

    # Emitted when values changed in memory but not yet written
    class Changed(Message):
        pass

    DEFAULT_CSS = ""  # CSS lives in MyrientBrowser.DEFAULT_CSS

    def __init__(self, config: Config, **kwargs) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._errors: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Schema: sections → rows
    # ------------------------------------------------------------------

    @staticmethod
    def _schema() -> list[tuple[str, list[tuple[str, str, str, str]]]]:
        """Return (section_label, [(field_id, label, type, hint), ...])."""
        return [
            ("Server", [
                ("server.base_url",         "Base URL",             "str",   ""),
                ("server.user_agent",        "User-Agent",           "str",   ""),
            ]),
            ("Download", [
                ("download.download_dir",    "Download directory",   "str",   "path — relative to project root"),
                ("download.concurrency",     "Concurrency",          "int",   "1–32 parallel downloads  ★ live"),
                ("download.segments_per_file","Segments per file",   "int",   "parallel Range segments (1 = off)  ★ live"),
                ("download.retries",         "Retries",              "int",   "attempts per file on error"),
                ("download.retry_delay",     "Retry delay (s)",      "float", "base backoff in seconds"),
                ("download.max_retry_delay", "Max retry delay (s)",  "float", "backoff cap"),
                ("download.chunk_size",      "Chunk size (bytes)",   "int",   "512 KB = 524288"),
                ("download.timeout",         "Timeout (s)",          "int",   "per-request timeout"),
                ("download.rate_limit",      "Rate limit (req/s)",   "float", "0 = disabled"),
                ("download.min_segmented_mb","Min segmented (MB)",   "float", "file size threshold for segmenting"),
            ]),
            ("Index", [
                ("index.index_file",         "Index file",           "str",   "path to all_paths.json / .txt"),
                ("index.search_limit",       "Search limit",         "int",   "max fuzzy-search results"),
                ("index.watch_enabled",      "Watch for changes",    "bool",  "reload index on file change"),
                ("index.watch_interval",     "Watch interval (s)",   "int",   "seconds between index checks"),
            ]),
            ("Export", [
                ("export.export_dir",        "Export directory",     "str",   ""),
                ("export.default_filename",  "Default filename",     "str",   ""),
            ]),
            ("Display", [
                ("display.use_decimal_units","Decimal units (KB/MB)","bool",  "off = binary KiB/MiB  ★ live"),
                ("display.du_human_readable","du -h readable",       "bool",  "let du format output  ★ live"),
            ]),
            ("Logging", [
                ("logging.log_file",         "Log file",             "str",   ""),
                ("logging.log_level",        "Log level",            "str",   "DEBUG / INFO / WARNING / ERROR"),
            ]),
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_value(self, field_id: str) -> Any:
        section, key = field_id.split(".", 1)
        return getattr(getattr(self._config, section), key)

    def _set_value(self, field_id: str, raw: str, type_: str) -> str | None:
        """Parse raw string, set on config, return error string or None."""
        section, key = field_id.split(".", 1)
        obj = getattr(self._config, section)
        try:
            if type_ == "bool":
                # booleans are handled by Switch, raw is "true"/"false"
                value: Any = raw.lower() in ("true", "1", "on")
            elif type_ == "int":
                value = int(raw)
            elif type_ == "float":
                value = float(raw)
            else:
                value = raw
            setattr(obj, key, value)
            return None
        except (ValueError, TypeError) as exc:
            return str(exc)

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="settings-scroll"):
            for section_label, rows in self._schema():
                yield Static(
                    f"[bold cyan]── {section_label} {'─' * max(0, 42 - len(section_label))}[/bold cyan]",
                    classes="settings-section-header",
                )
                for field_id, label, type_, hint in rows:
                    row_id = f"settings-row-{field_id.replace('.', '-')}"
                    with Horizontal(classes="settings-row", id=row_id):
                        yield Label(label, classes="settings-label")
                        if type_ == "bool":
                            current = bool(self._get_value(field_id))
                            yield Switch(
                                value=current,
                                id=f"sf-{field_id.replace('.', '-')}",
                                classes="settings-switch",
                            )
                            yield Static(
                                f"[dim]{hint}[/dim]" if hint else "",
                                classes="settings-hint",
                            )
                        else:
                            current_str = str(self._get_value(field_id))
                            yield Input(
                                value=current_str,
                                id=f"sf-{field_id.replace('.', '-')}",
                                classes="settings-input",
                            )
                            yield Static(
                                f"[dim]{hint}[/dim]" if hint else "",
                                classes="settings-hint",
                            )

            # Action buttons
            with Horizontal(id="settings-buttons"):
                yield Button("💾  Save to config.toml  (S)", id="btn-settings-save", variant="success")
                yield Button("⚡  Apply live settings", id="btn-settings-apply", variant="primary")
                yield Button("↩  Reload from file", id="btn-settings-reload", variant="default")
            yield Static(
                "[dim]★ live = applies immediately without restart[/dim]",
                id="settings-footer-note",
            )
            yield Static("", id="settings-status")

    # ------------------------------------------------------------------
    # Gather & validate
    # ------------------------------------------------------------------

    def gather_and_validate(self) -> dict[str, str] | None:
        """Read all form widgets, validate, update config.

        Returns a dict of {field_id: error_msg} if there are validation
        errors, or None if everything is OK (config already updated).
        """
        errors: dict[str, str] = {}
        for _, rows in self._schema():
            for field_id, _, type_, _ in rows:
                widget_id = f"sf-{field_id.replace('.', '-')}"
                try:
                    if type_ == "bool":
                        widget = self.query_one(f"#{widget_id}", Switch)
                        raw = "true" if widget.value else "false"
                    else:
                        widget = self.query_one(f"#{widget_id}", Input)
                        raw = widget.value.strip()
                    err = self._set_value(field_id, raw, type_)
                    if err:
                        errors[field_id] = err
                except Exception as exc:
                    errors[field_id] = str(exc)
        return errors if errors else None

    def show_status(self, msg: str, style: str = "green") -> None:
        try:
            self.query_one("#settings-status", Static).update(
                f"[{style}]{msg}[/{style}]"
            )
        except Exception:
            pass

    def reload_from_config(self) -> None:
        """Re-populate all form widgets from the current config object."""
        for _, rows in self._schema():
            for field_id, _, type_, _ in rows:
                widget_id = f"sf-{field_id.replace('.', '-')}"
                try:
                    value = self._get_value(field_id)
                    if type_ == "bool":
                        self.query_one(f"#{widget_id}", Switch).value = bool(value)
                    else:
                        self.query_one(f"#{widget_id}", Input).value = str(value)
                except Exception:
                    pass


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

    #download-concurrency {
        height: auto;
        padding: 0 1;
        background: $surface-darken-2;
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

    #loading-indicator {
        width: 100%;
        height: 1;
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

    /* ── Settings tab ─────────────────────────────────────────── */
    #settings-panel {
        height: 1fr;
        padding: 0;
    }

    #settings-scroll {
        height: 1fr;
        padding: 1 2;
    }

    .settings-section-header {
        padding-top: 1;
        padding-bottom: 0;
    }

    .settings-row {
        height: 3;
        align: left middle;
        padding: 0 0 0 1;
    }

    .settings-label {
        width: 24;
        padding-right: 1;
        color: $text-muted;
        content-align: right middle;
    }

    .settings-input {
        width: 40;
        height: 3;
    }

    .settings-switch {
        width: 10;
        height: 3;
    }

    .settings-hint {
        width: 1fr;
        padding-left: 2;
        color: $text-disabled;
        content-align: left middle;
    }

    #settings-buttons {
        height: 5;
        padding: 1 1;
        align: left middle;
    }

    #settings-buttons Button {
        margin-right: 2;
    }

    #settings-footer-note {
        padding: 0 1;
        color: $text-disabled;
    }

    #settings-status {
        padding: 0 1;
        height: 2;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        # Global
        Binding("q", "quit", "Quit"),
        Binding("h", "show_help", "Help"),
        Binding("escape", "clear_or_back", "Clear/Back"),
        # Tab switching (uppercase, unused letters)
        Binding("B", "switch_to_browser", "Browser", show=False),
        Binding("L", "switch_to_downloads", "Downloads", show=False),
        Binding("O", "switch_to_settings", "Settings/Options", show=False),
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
        Binding("]", "next_page", "Next page", show=False, priority=True),
        Binding("[", "prev_page", "Prev page", show=False, priority=True),
        # Downloads tab
        Binding("p", "retry_selected", "Retry", show=False),
        Binding("u", "promote_selected", "Move to front", show=False),
        Binding("shift+f", "force_redownload", "Force re-download", show=False),
        Binding("x", "remove_download", "Remove", show=False),
        Binding("f", "retry_all_failed", "Retry All", show=False),
        Binding("k", "clear_completed", "Clear Done", show=False),
        Binding("X", "clear_all_downloads", "Clear All", show=False),
        Binding("1", "filter_all", "All", show=False),
        Binding("2", "filter_queued", "Queued", show=False),
        Binding("3", "filter_active", "Active", show=False),
        Binding("4", "filter_done", "Done", show=False),
        Binding("5", "filter_failed", "Failed", show=False),
        Binding("+", "concurrency_up", "More slots", show=False),
        Binding("-", "concurrency_down", "Fewer slots", show=False),
        Binding("P", "pause_all_downloads", "Pause all", show=False),
        Binding("R", "resume_all_downloads", "Resume all", show=False),
        Binding("T", "clear_throttle", "Clear throttle", show=False),
        Binding("S", "save_settings", "Save settings", show=False),
        # ~ / ` handled in on_key (Textual key names: tilde / grave_accent)
    ]

    download_search_query = reactive("")
    download_status_filter = reactive("all")

    show_only_missing = reactive(False)
    current_path = reactive("")
    search_query = reactive("")
    index_loading = reactive(True)

    LIST_PAGE_SIZE = 500

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
        self._search_debounce_timer = None
        self._all_nodes: list[IndexNode] = []  # Full untruncated node list
        self._list_page: int = 0  # Current page for large directories
        self._download_page: int = 0  # Current page for Downloads tab
        self._download_all_items: list = []  # Full unfiltered+filtered list for pagination

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

    def _is_settings_tab(self) -> bool:
        try:
            tabs = self.query_one(TabbedContent)
            return tabs.active == "tab-settings"
        except Exception:
            return False

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
                with TabbedContent(id="main-tabs"):
                    with TabPane("[underline]B[/underline]rowser", id="tab-browser"):
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

                    with TabPane("Down[underline]l[/underline]oads", id="tab-downloads"):
                        yield DownloadPanel(id="download-panel-content")

                    with TabPane("[underline]O[/underline]ptions", id="tab-settings"):
                        yield SettingsPanel(self.config, id="settings-panel")

            with Vertical(id="side-panel"):
                yield InfoPanel(id="info-panel")
                yield Static("", id="stats-panel")

        yield Footer()

    # Cached output of `du [-h] -s downloads/` updated every 15 s in background
    _du_result: str = ""

    def on_mount(self) -> None:
        """Initialize on mount."""
        self.title = "Myrient Browser"
        self.sub_title = "Use responsibly - only download content you have rights to"

        # Ensure stats cache is consistent with actual state
        self.state.rebuild_stats()

        # Start downloader immediately (doesn't need index)
        self.start_downloader()
        self.set_interval(1.0, self.update_download_panel)
        # Kick off du refresh; first call is immediate, then every 15 s
        self._refresh_du()
        self.set_interval(15.0, self._refresh_du)

        # Load index in background if not already loaded
        if self.index is None:
            self.loading_screen = LoadingScreen()
            self.push_screen(self.loading_screen)
            self.load_index_async()
        else:
            self._finish_index_load()

    @work(thread=True)
    def _refresh_du(self) -> None:
        """Run `du [-h] -s <downloads_dir>` in a background thread and cache result."""
        try:
            dl_dir = self.config.get_download_dir()
            if not dl_dir.exists():
                self._du_result = "0 B (empty)"
                self.call_from_thread(self.update_stats)
                return
            use_h = getattr(self.config.display, "du_human_readable", False)
            args = ["du", "-sh", str(dl_dir)] if use_h else ["du", "-s", str(dl_dir)]
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                raw = result.stdout.split("\t")[0].strip()
                if use_h:
                    self._du_result = raw          # e.g. "4.2G"
                else:
                    # raw is block count; convert to bytes (512-byte blocks on macOS,
                    # 1024-byte on Linux — detect via sys.platform)
                    try:
                        blocks = int(raw)
                        block_size = 512 if sys.platform == "darwin" else 1024
                        total_bytes = blocks * block_size
                        self._du_result = format_size(total_bytes)
                    except ValueError:
                        self._du_result = raw
        except Exception:  # noqa: BLE001
            pass
        self.call_from_thread(self.update_stats)

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

    def refresh_list(self, preserve_cursor: bool = False, reset_page: bool = True) -> None:
        """Refresh the file list. For search queries, dispatches to a background worker."""
        if self.index is None or self.index_loading:
            list_view = self.query_one("#file-list", ListView)
            list_view.clear()
            self.query_one("#path-display", Static).update("[yellow]Loading index...[/yellow]")
            self.current_items = []
            return

        if reset_page:
            self._list_page = 0

        if self.search_query:
            # Run search in a background thread to avoid blocking the event loop
            self._run_search(self.search_query, preserve_cursor)
        else:
            nodes = self.index.get_children(self.current_path)
            if self.show_only_missing:
                nodes = [n for n in nodes if check_download_status(self.config, n.path) != "DOWNLOADED"]
            label = f"/{self.current_path}" if self.current_path else "/"
            self._populate_list(nodes, label, preserve_cursor)

    @work(exclusive=True, thread=True)
    def _run_search(self, query: str, preserve_cursor: bool = False) -> None:
        """Run search in a background thread, then update UI from main thread."""
        if not self.index:
            return
        nodes = self.index.search(query, limit=200)
        if self.show_only_missing:
            nodes = [n for n in nodes if check_download_status(self.config, n.path) != "DOWNLOADED"]
        label = f"Search: {query} ({len(nodes)} results)"
        self.call_from_thread(self._apply_search_results, query, nodes, label, preserve_cursor)

    def _apply_search_results(
        self, query: str, nodes: list, label: str, preserve_cursor: bool
    ) -> None:
        """Apply search results to UI (must be called from main thread)."""
        if query != self.search_query:
            return
        self._populate_list(nodes, label, preserve_cursor)

    @work(exclusive=True)
    async def _populate_list(self, nodes: list, path_label: str, preserve_cursor: bool = False) -> None:
        """Populate the file list with nodes and update UI.

        Runs as an exclusive async worker so concurrent calls are cancelled,
        preventing stale-widget crashes (ListView.clear() is async in Textual).
        Large directories are paginated to LIST_PAGE_SIZE items.
        """
        list_view = self.query_one("#file-list", ListView)
        path_display = self.query_one("#path-display", Static)

        old_index = list_view.index if preserve_cursor else None

        self._all_nodes = nodes
        total = len(nodes)

        # Apply pagination
        page_size = self.LIST_PAGE_SIZE
        start = self._list_page * page_size
        if start >= total and total > 0:
            self._list_page = 0
            start = 0
        end = min(start + page_size, total)
        page_nodes = nodes[start:end]

        self.current_items = page_nodes

        has_sizes = self.index.has_sizes if self.index else False
        items: list[PathItem] = []
        for node in page_nodes:
            is_selected = node.path in self.selected_paths
            status = check_download_status(self.config, node.path) if not node.is_dir else "MISSING"
            size = (self.index.get_dir_size(node.path) if node.is_dir else node.size) if has_sizes else -1
            items.append(PathItem(node, selected=is_selected, download_status=status, size=size))

        # Await clear so old widgets are fully removed before new ones are added.
        # This prevents the ValueError from stale PathItem references in _nodes.
        await list_view.clear()
        if items:
            await list_view.mount(*items)

        if old_index is not None and page_nodes:
            list_view.index = min(old_index, len(page_nodes) - 1)

        # Show pagination info if the directory is too large
        if total > page_size:
            pages = (total + page_size - 1) // page_size
            label = (
                f"{path_label}  [dim]{start + 1}-{end}/{total}, str. {self._list_page + 1}/{pages}[/dim]"
                f"  [cyan]][/cyan][dim] nast.[/dim]"
                f"  [cyan]\\[[/cyan][dim] poprz.[/dim]"
            )
        else:
            label = path_label
        path_display.update(label)
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

        du_line = f"\n[bold]Disk used:[/bold] {self._du_result}" if self._du_result else ""

        paused_q = queue_stats.get("paused", 0)
        paused_str = f", [yellow]{paused_q} paused[/yellow]" if paused_q > 0 else ""
        stats = (
            f"[bold]Index:[/bold] {index_info}\n"
            f"[bold]Selected:[/bold] {selected_count}{selected_size_str}"
            f"{current_folder_info}\n"
            f"[bold]Queue:[/bold] {queue_stats['queued']} queued"
            f"{paused_str}, {queue_stats['downloading']} active\n"
            f"[bold]Done:[/bold] {queue_stats['completed']} completed, "
            f"{queue_stats['failed']} failed"
            f"{du_line}"
        )

        stats_panel.update(stats)

    def update_download_panel(self, reset_page: bool = False) -> None:
        """Update download panel with search, status filtering and pagination."""
        try:
            panel = self.query_one("#download-panel-content", DownloadPanel)
        except Exception:
            return

        try:
            all_items = self.state.get_all_items()
            stats = self.state.get_stats()

            # Apply status filter
            status_filter = self.download_status_filter
            if status_filter == "queued":
                items = [i for i in all_items if i.status in (DownloadStatus.QUEUED, DownloadStatus.PAUSED)]
            elif status_filter == "active":
                items = [i for i in all_items if i.status == DownloadStatus.DOWNLOADING]
            elif status_filter == "done":
                items = [i for i in all_items if i.status in (DownloadStatus.COMPLETED, DownloadStatus.ALREADY_DOWNLOADED)]
            elif status_filter == "failed":
                items = [i for i in all_items if i.status == DownloadStatus.FAILED]
            else:
                items = all_items

            # Apply search filter
            search_query = self.download_search_query.strip().lower()
            if search_query:
                items = [
                    i for i in items
                    if search_query in Path(i.path).name.lower() or search_query in i.path.lower()
                ]

            # Sort: downloading first, then queued by priority, then by added time
            items.sort(key=lambda x: (
                x.status != DownloadStatus.DOWNLOADING,
                x.status != DownloadStatus.QUEUED,
                x.priority if x.status == DownloadStatus.QUEUED else 0,
                -x.added_at,
            ))

            # Save full filtered list for pagination
            self._download_all_items = items
            total = len(items)

            if reset_page:
                self._download_page = 0

            # Pagination
            page_size = self.LIST_PAGE_SIZE
            page = self._download_page
            max_page = max(0, (total - 1) // page_size) if total > 0 else 0
            if page > max_page:
                self._download_page = max_page
                page = max_page
            start = page * page_size
            end = min(start + page_size, total)
            page_items = items[start:end]

            # Sizes must be computed from the FULL filtered list, not just the
            # current page, so that Total/Remaining reflect all queued work.
            all_total_size = sum(i.total_size for i in items if i.total_size > 0)
            all_downloaded_size = sum(i.downloaded_size for i in items)

            # Build stats for summary
            filtered_stats = stats.copy()
            filtered_stats["filtered"] = total
            filtered_stats["page_start"] = start
            filtered_stats["page_end"] = end
            filtered_stats["page"] = page
            filtered_stats["max_page"] = max_page
            filtered_stats["all_total_size"] = all_total_size
            filtered_stats["all_downloaded_size"] = all_downloaded_size

            panel.update_downloads(page_items, filtered_stats)

            # Update concurrency bar
            if self.downloader:
                panel.update_concurrency(
                    self.downloader.concurrency,
                    self.downloader.throttle_remaining,
                    self.downloader.paused_all,
                )

            self.update_stats()
        except Exception:
            pass

    @on(Input.Changed, "#search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        """Handle search input change with debounce to avoid hammering the index."""
        self.search_query = event.value
        # Cancel pending debounce timer
        if self._search_debounce_timer is not None:
            self._search_debounce_timer.stop()
            self._search_debounce_timer = None
        if not event.value:
            # Empty query - show directory immediately
            self.refresh_list()
        else:
            # Debounce: wait 300ms after last keystroke before searching
            self._search_debounce_timer = self.set_timer(0.3, self.refresh_list)

    @on(Switch.Changed, "#missing-switch")
    def on_missing_switch_changed(self, event: Switch.Changed) -> None:
        """Handle missing filter toggle."""
        self.show_only_missing = event.value
        self.refresh_list()

    @on(Input.Changed, "#download-search")
    def on_download_search_changed(self, event: Input.Changed) -> None:
        """Handle download search input change."""
        self.download_search_query = event.value
        self.update_download_panel(reset_page=True)

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

        # Extract parent path directly from the full node path
        if "/" in node.path:
            parent_path = node.path.rsplit("/", 1)[0]
            target_name = node.path.rsplit("/", 1)[1]
        else:
            parent_path = ""
            target_name = node.name

        # Clear search and navigate directly to the parent folder
        self.search_query = ""
        try:
            self.query_one("#search-input", Input).value = ""
        except Exception:
            pass
        self.current_path = parent_path
        self.refresh_list()

        # Highlight the item after the screen has fully refreshed
        self.call_after_refresh(self._highlight_item, target_name)

    def _highlight_item(self, target_name: str) -> None:
        """Highlight item by name in current list."""
        try:
            list_view = self.query_one("#file-list", ListView)
            for idx, node in enumerate(self.current_items):
                if node.name == target_name:
                    list_view.index = idx
                    break
        except Exception:
            pass

    def on_key(self, event: Key) -> None:
        """Handle special keys that Textual won't match by character alone."""
        if event.key == "right_square_bracket":
            event.prevent_default()
            self.action_next_page()
        elif event.key == "left_square_bracket":
            event.prevent_default()
            self.action_prev_page()
        elif event.key in ("tilde", "grave_accent"):
            # ~ (Shift+`) or ` both launch the screensaver
            event.prevent_default()
            self.action_screensaver()

    def action_next_page(self) -> None:
        """Go to next page (Browser or Downloads tab)."""
        if self._is_downloads_tab():
            total = len(self._download_all_items)
            max_page = max(0, (total - 1) // self.LIST_PAGE_SIZE) if total else 0
            if self._download_page < max_page:
                self._download_page += 1
                self.update_download_panel()
            else:
                self.notify("Last page", severity="information")
        else:
            total = len(self._all_nodes)
            pages = (total + self.LIST_PAGE_SIZE - 1) // self.LIST_PAGE_SIZE
            if self._list_page < pages - 1:
                self._list_page += 1
                self.refresh_list(preserve_cursor=False, reset_page=False)
            else:
                self.notify("Last page", severity="information")

    def action_prev_page(self) -> None:
        """Go to previous page (Browser or Downloads tab)."""
        if self._is_downloads_tab():
            if self._download_page > 0:
                self._download_page -= 1
                self.update_download_panel()
            else:
                self.notify("First page", severity="information")
        else:
            if self._list_page > 0:
                self._list_page -= 1
                self.refresh_list(preserve_cursor=False, reset_page=False)
            else:
                self.notify("First page", severity="information")

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
        """Add paths to download queue, including file sizes from the index."""
        if self.downloader:
            sizes: dict[str, int] = {}
            if self.index and self.index.has_sizes:
                for path in paths:
                    info = self.index._path_info.get(path)
                    if info and not info[0]:  # file, not directory
                        sizes[path] = info[1]
            added_new, already_present = await self.downloader.add_to_queue(paths, sizes=sizes)
            if already_present and added_new:
                self.notify(
                    f"Added {added_new} to queue, {already_present} already on disk (press [F] to force re-download)",
                    severity="warning",
                )
            elif already_present:
                self.notify(
                    f"{already_present} file(s) already on disk — skipped. Press [F] to force re-download.",
                    severity="warning",
                )
            else:
                self.notify(f"Added {added_new} files to queue")

            # Clear selection after adding to queue
            if self.selected_paths:
                self.selected_paths.clear()
                self.refresh_list(preserve_cursor=True)

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
            if self.downloader:
                asyncio.create_task(self.downloader._process_queue())
            self.notify(f"Retrying {count} failed downloads")
            self.update_download_panel()
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
            self.update_download_panel()
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
        self.update_download_panel(reset_page=True)
        self._update_filter_display()

    def _update_filter_display(self) -> None:
        """Update filter buttons display to show active filter."""
        try:
            filter_widget = self.query_one("#download-filter-buttons", Static)
            f = self.download_status_filter
            parts = [
                f"[bold cyan][1]All[/]" if f == "all" else "[1]All",
                f"[bold cyan][2]Queued/Paused[/]" if f == "queued" else "[2]Queued/Paused",
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
                    self.state.promote_item(item.path)
                    self.state.save(force=True)
                    self.notify(f"↑ Restarting (priority): {Path(item.path).name}")
                elif item.status in (DownloadStatus.FAILED, DownloadStatus.PAUSED):
                    self.state.update_item(
                        item.path,
                        status=DownloadStatus.QUEUED,
                        progress=0.0,
                        downloaded_size=0,
                        error="",
                        retries=0,
                    )
                    self.state.promote_item(item.path)
                    self.state.save(force=True)
                    self.notify(f"↑ Retrying (priority): {Path(item.path).name}")
                elif item.status == DownloadStatus.COMPLETED:
                    if local_path.exists():
                        local_path.unlink()
                    self.state.update_item(
                        item.path,
                        status=DownloadStatus.QUEUED,
                        progress=0.0,
                        downloaded_size=0,
                        error="",
                    )
                    self.state.promote_item(item.path)
                    self.state.save(force=True)
                    self.notify(f"↑ Re-downloading (priority): {Path(item.path).name}")
                elif item.status == DownloadStatus.ALREADY_DOWNLOADED:
                    if local_path.exists():
                        local_path.unlink()
                    self.state.update_item(
                        item.path,
                        status=DownloadStatus.QUEUED,
                        progress=0.0,
                        downloaded_size=0,
                        error="",
                        retries=0,
                    )
                    self.state.promote_item(item.path)
                    self.state.save(force=True)
                    self.notify(f"↑ Re-downloading (priority): {Path(item.path).name}")
                elif item.status == DownloadStatus.QUEUED:
                    self.notify("Already queued — use [u] to move to front", severity="warning")
            else:
                self.notify("No download selected", severity="warning")
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")

    async def action_force_redownload(self) -> None:
        """Force re-download of selected item regardless of current status (F).

        Works in both Downloads tab (selected row) and Browser tab (highlighted file).
        """
        logger.info(f"action_force_redownload called: downloads={self._is_downloads_tab()}, browser={self._is_browser_tab()}")
        if not self.downloader:
            logger.warning("action_force_redownload: no downloader")
            return
        try:
            if self._is_downloads_tab():
                panel = self.query_one("#download-panel-content", DownloadPanel)
                item = panel.get_selected_item()
                if not item:
                    self.notify("No download selected", severity="warning")
                    return
                ok = await self.downloader.force_redownload(item.path)
                if ok:
                    self.notify(f"↑ Force re-download queued: {Path(item.path).name}", severity="warning")
                else:
                    self.notify("Item not found in queue", severity="error")
            elif self._is_browser_tab():
                paths: list[str] = []
                if self.selected_paths:
                    paths = list(self.selected_paths)
                else:
                    list_view = self.query_one("#file-list", ListView)
                    if list_view.highlighted_child and isinstance(list_view.highlighted_child, PathItem):
                        paths = [list_view.highlighted_child.node.path]
                logger.info(f"action_force_redownload: paths={paths}")
                if not paths or not self.index:
                    self.notify("Nothing selected", severity="warning")
                    return
                # Expand directories to files
                expanded = self.index.expand_selection(paths)
                logger.info(f"action_force_redownload: expanded to {len(expanded)} files")
                if not expanded:
                    self.notify("No files to download", severity="warning")
                    return
                count = 0
                sizes: dict[str, int] = {}
                if self.index.has_sizes:
                    for path in expanded:
                        info = self.index._path_info.get(path)
                        if info and not info[0]:
                            sizes[path] = info[1]
                for path in expanded:
                    existing = self.downloader.state.get_item(path)
                    if existing:
                        ok = await self.downloader.force_redownload(path)
                        if ok:
                            count += 1
                    else:
                        await self.downloader.add_to_queue([path], sizes=sizes, force=True)
                        count += 1
                if count:
                    self.notify(f"↑ Force re-download: {count} file(s) queued", severity="warning")
                    self.update_stats()
        except Exception as e:
            import traceback
            logger.error(f"action_force_redownload error: {e}\n{traceback.format_exc()}")
            self.notify(f"Error: {e}", severity="error")

    def action_concurrency_up(self) -> None:
        """Increase concurrent download slots by 1 (+)."""
        if not self._is_downloads_tab() or not self.downloader:
            return
        new = self.downloader.set_concurrency(self.downloader.concurrency + 1)
        self.notify(f"Concurrency → {new}", severity="information")
        self.update_download_panel()

    def action_concurrency_down(self) -> None:
        """Decrease concurrent download slots by 1 (-)."""
        if not self._is_downloads_tab() or not self.downloader:
            return
        new = self.downloader.set_concurrency(self.downloader.concurrency - 1)
        self.notify(f"Concurrency → {new}", severity="information")
        self.update_download_panel()

    async def action_pause_all_downloads(self) -> None:
        """Freeze all existing queued/active items; new items still download [P]."""
        if not self._is_downloads_tab() or not self.downloader:
            return
        stats = self.state.get_stats()
        candidates = stats.get("queued", 0) + stats.get("downloading", 0)
        if candidates == 0:
            self.notify("Nothing to pause (no queued or active downloads)", severity="warning")
            return
        await self.downloader.pause_all()
        self.notify(
            f"⏸  {candidates} item(s) paused — new downloads still start normally.  [R] to resume.",
            severity="warning",
        )
        self.update_download_panel()

    async def action_resume_all_downloads(self) -> None:
        """Move all PAUSED items back to QUEUED [R]."""
        if not self._is_downloads_tab() or not self.downloader:
            return
        paused_count = self.state.get_stats().get("paused", 0)
        if paused_count == 0:
            self.notify("No paused items to resume", severity="information")
            return
        await self.downloader.resume_all()
        self.notify(f"▶  {paused_count} item(s) resumed.", severity="information")
        self.update_download_panel()

    def action_clear_throttle(self) -> None:
        """Clear server-imposed rate-limit throttle immediately [T]."""
        if not self._is_downloads_tab() or not self.downloader:
            return
        remaining = self.downloader.throttle_remaining
        if remaining <= 0:
            self.notify("No active throttle to clear", severity="information")
            return
        self.downloader.clear_throttle()
        self.notify(
            f"⚡ Throttle cleared ({remaining:.0f}s skipped) — downloads resuming",
            severity="warning",
        )
        self.update_download_panel()

    def action_promote_selected(self) -> None:
        """Move selected download to the front of the queue."""
        if not self._is_downloads_tab():
            return
        try:
            panel = self.query_one("#download-panel-content", DownloadPanel)
            item = panel.get_selected_item()
            if not item:
                self.notify("No download selected", severity="warning")
                return
            if item.status != DownloadStatus.QUEUED:
                self.notify("Only queued items can be moved to front", severity="warning")
                return
            if self.state.promote_item(item.path):
                self.state.save(force=True)
                self.notify(f"↑ Moved to front: {Path(item.path).name}")
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")

    def action_screensaver(self) -> None:
        """Launch the screensaver (~)."""
        self.push_screen(ScreensaverScreen(self.state))

    # ------------------------------------------------------------------
    # Tab switching actions
    # ------------------------------------------------------------------

    def action_switch_to_browser(self) -> None:
        """Switch to Browser tab (b)."""
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
            tabs.active = "tab-browser"
        except Exception:
            pass

    def action_switch_to_downloads(self) -> None:
        """Switch to Downloads tab (D)."""
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
            tabs.active = "tab-downloads"
        except Exception:
            pass

    def action_switch_to_settings(self) -> None:
        """Switch to Settings tab (s)."""
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
            tabs.active = "tab-settings"
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Settings tab actions
    # ------------------------------------------------------------------

    def _get_settings_panel(self) -> SettingsPanel | None:
        try:
            return self.query_one("#settings-panel", SettingsPanel)
        except Exception:
            return None

    def _apply_live_settings(self) -> list[str]:
        """Apply settings that take effect immediately without restart.

        Returns a list of human-readable change descriptions.
        """
        applied: list[str] = []

        # Concurrency — can be changed on a live DownloadManager
        if self.downloader:
            new_c = self.config.download.concurrency
            if self.downloader.concurrency != new_c:
                self.downloader.set_concurrency(new_c)
                applied.append(f"concurrency → {new_c}")

        # Display units — format_size() reads config on every call
        applied.append(
            "decimal units " + ("on" if self.config.display.use_decimal_units else "off")
        )

        # du_human_readable — next _refresh_du() will pick up the new value
        if self.config.display.du_human_readable:
            applied.append("du -h enabled")

        # Trigger an immediate du refresh and stats update
        self._refresh_du()
        self.update_stats()

        return applied

    def action_save_settings(self) -> None:
        """Validate form, update config, write config.toml, apply live [S]."""
        if not self._is_settings_tab():
            return
        panel = self._get_settings_panel()
        if panel is None:
            return

        errors = panel.gather_and_validate()
        if errors:
            msgs = "; ".join(f"{k}: {v}" for k, v in errors.items())
            panel.show_status(f"✗ Errors: {msgs}", style="red")
            self.notify(f"Settings error: {msgs[:80]}", severity="error")
            return

        try:
            self.config.save_to_toml()
        except Exception as exc:
            panel.show_status(f"✗ Save failed: {exc}", style="red")
            self.notify(f"Save failed: {exc}", severity="error")
            return

        applied = self._apply_live_settings()
        panel.show_status(
            f"✓ Saved to config.toml  |  live: {', '.join(applied)}", style="green"
        )
        self.notify("Settings saved ✓", severity="information")

    @on(Button.Pressed, "#btn-settings-save")
    def _on_settings_save(self, _event: Button.Pressed) -> None:
        self.action_save_settings()

    @on(Button.Pressed, "#btn-settings-apply")
    def _on_settings_apply(self, _event: Button.Pressed) -> None:
        panel = self._get_settings_panel()
        if panel is None:
            return
        errors = panel.gather_and_validate()
        if errors:
            msgs = "; ".join(f"{k}: {v}" for k, v in errors.items())
            panel.show_status(f"✗ Errors: {msgs}", style="red")
            return
        applied = self._apply_live_settings()
        panel.show_status(
            f"⚡ Applied live: {', '.join(applied)}  (not saved to file)", style="yellow"
        )
        self.notify("Live settings applied ⚡", severity="information")

    @on(Button.Pressed, "#btn-settings-reload")
    def _on_settings_reload(self, _event: Button.Pressed) -> None:
        """Reload config from file and refresh form widgets."""
        panel = self._get_settings_panel()
        if panel is None:
            return
        try:
            config_path = self.config.project_root / "config.toml"
            if config_path.exists():
                self.config._load_from_file(config_path)
                panel.reload_from_config()
                panel.show_status("↩ Reloaded from config.toml", style="cyan")
                self.notify("Config reloaded", severity="information")
            else:
                panel.show_status("config.toml not found — using defaults", style="yellow")
        except Exception as exc:
            panel.show_status(f"✗ Reload failed: {exc}", style="red")

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
