"""ProgressOverlay <-> CLI integration seam (Slice-0 tracer).

Closes the loop through the layer that ships: real ``cli.main()`` over a real file, with a
Console we control and ``compute_ed2k`` wrapped to record the exact ``progress_callback``
passed. Tracer scope: the reconnect wiring — an active run feeds ``ProgressOverlay.on_progress``;
a suppressed run (non-TTY or ``--no-progress``) feeds ``None`` (zero per-chunk work). Off-TTY
parity byte-assertions and rendering are deferred to later slices.
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

import anipyrenamer.cli as cli
from anipyrenamer.progress_overlay import ProgressOverlay


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    terminal: bool,
    extra_args: tuple[str, ...] = (),
) -> tuple[list[object], str]:
    """Drive the real CLI over one video file; return the callbacks seen + all output."""
    seen: list[object] = []
    real_compute = cli.compute_ed2k

    def wrapped(path: str, *, progress_callback: object = None) -> str:
        seen.append(progress_callback)
        return real_compute(path, progress_callback=progress_callback)  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "compute_ed2k", wrapped)

    buffer = StringIO()
    console = Console(file=buffer, force_terminal=terminal or None, width=100)
    monkeypatch.setattr(cli, "Console", lambda *a, **k: console)

    scan_root = tmp_path / "show"
    scan_root.mkdir()
    (scan_root / "ep01.mkv").write_bytes(b"anime-payload-bytes" * 500)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "anipyrenamer",
            str(scan_root),
            "--offline",
            "--dry-run",
            "--db",
            str(tmp_path / "cli.sqlite3"),
            *extra_args,
        ],
    )
    try:
        cli.main()
    except SystemExit:
        pass
    return seen, buffer.getvalue()


def test_cli_feeds_overlay_callback_when_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On an interactive terminal, the ED2K callback is ProgressOverlay.on_progress."""
    seen, _ = _run(tmp_path, monkeypatch, terminal=True)
    non_none = [cb for cb in seen if cb is not None]
    assert non_none, "expected at least one non-None progress_callback on a TTY run"
    assert all(getattr(cb, "__func__", None) is ProgressOverlay.on_progress for cb in non_none)


def test_cli_passes_none_when_not_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off-TTY, the overlay is suppressed and no callback work is fed to hashing."""
    seen, _ = _run(tmp_path, monkeypatch, terminal=False)
    assert seen, "compute_ed2k should have been called"
    assert all(cb is None for cb in seen)


def test_cli_no_progress_flag_suppresses_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--no-progress suppresses the overlay even on an interactive terminal."""
    seen, _ = _run(tmp_path, monkeypatch, terminal=True, extra_args=("--no-progress",))
    assert seen, "compute_ed2k should have been called"
    assert all(cb is None for cb in seen)


def test_cli_renders_lookup_row_on_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TTY run drives the lookup row; offline collapses it to the cache-only token.

    'cache-only' is emitted only by the overlay's lookup row, never by the ledger, so its
    presence proves settle_lookup was wired through the real CLI.
    """
    _, out = _run(tmp_path, monkeypatch, terminal=True)
    assert "cache-only" in out


def test_cli_lookup_row_absent_off_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off-TTY there is no overlay, so the lookup-row token never appears."""
    _, out = _run(tmp_path, monkeypatch, terminal=False)
    assert "cache-only" not in out
