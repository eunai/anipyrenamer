"""ProgressOverlay unit seam (Slice-0 tracer).

Drives the overlay against an injected Rich ``Console`` (force_terminal + StringIO) so
terminal detection and captured output are fully controlled. Tracer scope: the
enabled/disabled lifecycle, the reconnected ``on_progress`` update, and that a disabled
overlay is completely inert. Columns, the lookup row, and byte-exact transient-clear
assertions are deferred to later slices.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from anipyrenamer.progress_overlay import LinkState, ProgressOverlay


def _console(*, terminal: bool) -> tuple[Console, StringIO]:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=terminal or None, width=100)
    return console, buffer


def test_disabled_overlay_is_inert() -> None:
    """A suppressed overlay never activates and writes nothing at all."""
    console, buffer = _console(terminal=True)
    overlay = ProgressOverlay(console, total=2, enabled=False)
    with overlay as ov:
        assert ov.active is False
        ov.begin_hash(1, "ep01.mkv", 1000)
        ov.on_progress(500, 1000)
        ov.advance_overall(1)
    assert ov.active is False
    assert buffer.getvalue() == ""


def test_enabled_overlay_renders_overall_and_file() -> None:
    """An enabled overlay paints the overall label and the per-file basename."""
    console, buffer = _console(terminal=True)
    overlay = ProgressOverlay(console, total=2, enabled=True)
    with overlay as ov:
        assert ov.active is True
        ov.begin_hash(1, "ep01.mkv", 1000)
        ov.on_progress(1000, 1000)
        ov.advance_overall(1)
    out = buffer.getvalue()
    assert "Hashing and lookup" in out
    assert "ep01.mkv" in out


def test_overlay_lifecycle_flips_active_true_then_false() -> None:
    """active is True inside the context and False after it exits (transient teardown)."""
    console, _ = _console(terminal=True)
    overlay = ProgressOverlay(console, total=1, enabled=True)
    assert overlay.active is False
    with overlay as ov:
        assert ov.active is True
    assert overlay.active is False


def test_per_file_row_shows_byte_progress() -> None:
    """The per-file row carries byte progress (DownloadColumn), not just a bar."""
    console, buffer = _console(terminal=True)
    with ProgressOverlay(console, total=1, enabled=True) as ov:
        ov.begin_hash(1, "ep01.mkv", 1_000_000)
        ov.on_progress(500_000, 1_000_000)
    out = buffer.getvalue()
    # DownloadColumn renders decimal byte units for completed/total.
    assert "MB" in out


def test_hostile_filename_renders_literally() -> None:
    """A basename with Rich markup and a control byte is neutralised, not interpreted."""
    console, buffer = _console(terminal=True)
    with ProgressOverlay(console, total=1, enabled=True) as ov:
        ov.begin_hash(1, "[bold]ep\x1b[31m01.mkv", 1000)
        ov.on_progress(1000, 1000)
    out = buffer.getvalue()
    # Escaped markup renders as literal brackets (interpreted markup would consume them).
    assert "[bold]" in out
    # The control byte is replaced, so the filename's tail shows as inert text ...
    assert "?[31m01.mkv" in out
    # ... and the injected escape sequence (ESC + that tail) never reaches the terminal.
    # (A bare \x1b[31m also legitimately appears as Rich's own speed-column colour, so we
    # anchor on the filename's tail rather than the SGR alone.)
    assert "\x1b[31m01.mkv" not in out


def test_settle_lookup_renders_counts_and_link_state() -> None:
    """With an AniDB client, the lookup row shows counts and session/enc/throttle."""
    console, buffer = _console(terminal=True)
    with ProgressOverlay(console, total=3, enabled=True) as ov:
        ov.settle_lookup(2, 1, 0, LinkState(session=True, encryption=True, throttle_seconds=2.0))
    out = buffer.getvalue()
    assert "2 cached" in out
    assert "1 fetched" in out
    assert "session on" in out
    assert "enc aes" in out
    assert "thr 2s" in out


def test_settle_lookup_collapses_without_a_client() -> None:
    """Offline (no client), the lookup row collapses to a single honest token."""
    console, buffer = _console(terminal=True)
    with ProgressOverlay(console, total=1, enabled=True) as ov:
        ov.settle_lookup(0, 0, 1, None)
    out = buffer.getvalue()
    assert "cache-only" in out
    assert "1 no match" in out
    assert "session" not in out  # no client -> no session/enc/throttle tail


def test_settle_lookup_inert_when_disabled() -> None:
    """A disabled overlay renders no lookup row."""
    console, buffer = _console(terminal=False)
    with ProgressOverlay(console, total=1, enabled=False) as ov:
        ov.settle_lookup(0, 0, 1, None)
    assert buffer.getvalue() == ""


def test_console_print_during_active_overlay_is_preserved() -> None:
    """Inline output (--debug / error lines) printed while the overlay is live survives.

    Rich renders a same-console print above the live region, so no explicit
    before-external-output coordination is needed and the message is not swallowed by the
    transient teardown.
    """
    console, buffer = _console(terminal=True)
    with ProgressOverlay(console, total=1, enabled=True) as ov:
        ov.begin_hash(1, "ep01.mkv", 1000)
        console.print("inline-debug-marker")
        ov.on_progress(1000, 1000)
    out = buffer.getvalue()
    assert "inline-debug-marker" in out  # inline line preserved above the live region
    assert "ep01.mkv" in out  # overlay kept rendering around it
