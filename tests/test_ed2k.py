"""Tests for ED2K hashing."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from anipyrenamer.ed2k import ED2K_CHUNK_SIZE, compute_ed2k


def test_ed2k_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "empty"
    f.write_bytes(b"")
    h = compute_ed2k(str(f))
    assert len(h) == 32
    assert h.isupper() or h.islower()
    assert all(c in "0123456789ABCDEFabcdef" for c in h)


def test_ed2k_small_file(tmp_path: Path) -> None:
    f = tmp_path / "small"
    f.write_bytes(b"hello")
    h = compute_ed2k(str(f))
    assert len(h) == 32


def test_ed2k_chunk_size_constant() -> None:
    assert ED2K_CHUNK_SIZE == 9_728_000


def test_ed2k_deterministic(tmp_path: Path) -> None:
    f = tmp_path / "same"
    f.write_bytes(b"x" * 1000)
    assert compute_ed2k(str(f)) == compute_ed2k(str(f))


def test_ed2k_progress_callback(tmp_path: Path) -> None:
    """progress_callback is called with (bytes_read, total) at start and after each chunk."""
    f = tmp_path / "progress"
    f.write_bytes(b"x" * 100)
    calls: list[tuple[int, int]] = []

    def cb(bytes_read: int, total: int) -> None:
        calls.append((bytes_read, total))

    compute_ed2k(str(f), progress_callback=cb)
    assert len(calls) >= 1
    assert calls[0] == (0, 100)
    assert calls[-1] == (100, 100)
