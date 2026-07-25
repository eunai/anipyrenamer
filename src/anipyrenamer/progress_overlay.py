"""The hash+look progress overlay: a TTY-only, transient Rich display.

A thin context-manager over Rich ``Live`` / ``Progress`` / ``Group`` that sits *above* the
untouched Quiet Ledger during the ``hash+look`` phase (the v1.2.0 interaction model). It shows
overall ``Hashing and lookup N/total`` progress and a temporary per-file hashing row driven by
the reconnected ED2K ``progress_callback``; when the phase settles it clears itself, leaving the
ledger's permanent counter line.

Tracer scope (Slice 0): the overall row, the per-file row, and the reconnect. The v1.2.0 column
set, the lookup row, ``--debug``/error coexistence, and byte-exact transient behaviour arrive in
later slices. The overlay is strictly additive and TTY-only — when disabled it enters no ``Live``
and emits nothing, so the callback path adds no per-chunk work.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from types import TracebackType

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape as rich_escape
from rich.text import Text
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)


def _safe_label(text: str) -> str:
    """Render an attacker-influenced basename literally.

    Control characters (ESC, etc.) are dropped so they cannot inject terminal sequences, and
    Rich markup is escaped so ``[bold]`` shows as text rather than being interpreted.
    """
    cleaned = "".join(ch if ch.isprintable() else "?" for ch in text)
    return rich_escape(cleaned)


def _fmt_duration(seconds: float) -> str:
    """Format an elapsed duration as ``M:SS``."""
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"


@dataclass(frozen=True)
class LinkState:
    """The AniDB connection facts the lookup row reports — state, never secrets."""

    session: bool
    encryption: bool
    throttle_seconds: float


class _LookupRow:
    """The overlay's third row: a single mutable line of settled lookup facts."""

    def __init__(self) -> None:
        self._markup = ""

    def set(self, markup: str) -> None:
        self._markup = markup

    def __rich__(self) -> Text:
        return Text.from_markup(self._markup)


class ProgressOverlay:
    """Owns the Rich progress surface for one ``hash+look`` phase.

    Construct with ``enabled`` already decided by the caller's suppression predicate; a disabled
    overlay is completely inert. Use as a context manager spanning the phase loop.
    """

    def __init__(self, console: Console, *, total: int, enabled: bool) -> None:
        self._console = console
        self._total = total
        self._enabled = enabled
        self._live: Live | None = None
        self._overall: Progress | None = None
        self._file: Progress | None = None
        self._lookup: _LookupRow | None = None
        self._overall_task: TaskID | None = None
        self._file_task: TaskID | None = None
        self._start = 0.0

    @property
    def active(self) -> bool:
        """Whether the overlay is live (enabled and inside its context)."""
        return self._live is not None

    def __enter__(self) -> ProgressOverlay:
        if not self._enabled:
            return self
        self._overall = Progress(
            SpinnerColumn(),
            BarColumn(bar_width=24, style="blue", complete_style="green"),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=self._console,
        )
        self._file = Progress(
            SpinnerColumn(),
            BarColumn(bar_width=28, style="bright_magenta", complete_style="green"),
            TextColumn("[progress.description]{task.description}"),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(compact=True),
            console=self._console,
        )
        self._overall_task = self._overall.add_task(
            f"Hashing and lookup 0/{self._total}", total=max(1, self._total)
        )
        self._lookup = _LookupRow()
        self._start = time.monotonic()
        self._live = Live(
            Group(self._overall, self._file, self._lookup),
            console=self._console,
            transient=True,
            refresh_per_second=8,
        )
        self._live.start()
        return self

    def begin_hash(self, ordinal: int, basename: str, size: int) -> None:
        """Start (or restart) the per-file row for the file now being hashed."""
        if self._live is None or self._file is None:
            return
        if self._file_task is not None:
            self._file.remove_task(self._file_task)
        self._file_task = self._file.add_task(
            f"[yellow]{_safe_label(basename)}[/yellow]", total=max(1, size), completed=0
        )

    def on_progress(self, done: int, total: int) -> None:
        """ED2K progress callback: advance the per-file row and repaint."""
        if self._live is None or self._file is None or self._file_task is None:
            return
        self._file.update(self._file_task, completed=done, total=max(1, total))
        self._live.refresh()

    def advance_overall(self, ordinal: int) -> None:
        """Mark ``ordinal`` groups through the phase on the overall row."""
        if self._live is None or self._overall is None or self._overall_task is None:
            return
        self._overall.update(
            self._overall_task,
            completed=ordinal,
            description=f"Hashing and lookup {ordinal}/{self._total}",
        )
        self._live.refresh()

    def settle_lookup(
        self, cached: int, fetched: int, no_match: int, link: LinkState | None
    ) -> None:
        """Repaint the lookup row after a file settles.

        With a client the tail is ``session … | enc … | thr …s``; without one it collapses to a
        single ``cache-only`` token. Reports connection *state*, never secrets.
        """
        if self._live is None or self._lookup is None:
            return
        done = cached + fetched + no_match
        parts = [
            f"lookup {done}/{self._total}",
            f"run {_fmt_duration(time.monotonic() - self._start)}",
            f"{cached} cached",
            f"{fetched} fetched",
            f"{no_match} no match",
        ]
        if link is None:
            parts.append("cache-only")
        else:
            parts.append(f"session {'on' if link.session else 'off'}")
            parts.append(f"enc {'aes' if link.encryption else 'plain'}")
            parts.append(f"thr {int(link.throttle_seconds)}s")
        self._lookup.set("[dim]" + " | ".join(parts) + "[/dim]")
        self._live.refresh()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._live is not None:
            if self._file is not None and self._file_task is not None:
                self._file.remove_task(self._file_task)
                self._file_task = None
            self._live.stop()  # transient=True clears the region on stop
            self._live = None
