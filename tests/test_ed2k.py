"""Tests for ED2K hashing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from anipyrenamer.ed2k import (
    ED2K_CHUNK_SIZE,
    MMAP_THRESHOLD,
    _hash_mmap,
    _hash_read,
    compute_ed2k,
)

# ── Known ED2K vectors ──────────────────────────────────────────────

EMPTY_ED2K = "31D6CFE0D16AE931B73C59D7E0C089C0"
HELLO_ED2K = "866437CB7A794BCE2B727ACC0362EE27"
MULTI_CHUNK_ED2K = "06329E9DBA1373512C06386FE29E3C65"


def test_ed2k_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "empty"
    f.write_bytes(b"")
    assert compute_ed2k(str(f)) == EMPTY_ED2K


def test_ed2k_single_chunk_exact(tmp_path: Path) -> None:
    f = tmp_path / "hello"
    f.write_bytes(b"hello")
    assert compute_ed2k(str(f)) == HELLO_ED2K


def test_ed2k_multi_chunk_exact(tmp_path: Path) -> None:
    """Two chunks: ED2K_CHUNK_SIZE zero-bytes + 1 extra byte."""
    f = tmp_path / "multi"
    f.write_bytes(b"\x00" * (ED2K_CHUNK_SIZE + 1))
    assert compute_ed2k(str(f)) == MULTI_CHUNK_ED2K


# ── Basic property tests ────────────────────────────────────────────


def test_ed2k_chunk_size_constant() -> None:
    assert ED2K_CHUNK_SIZE == 9_728_000


def test_ed2k_deterministic(tmp_path: Path) -> None:
    f = tmp_path / "same"
    f.write_bytes(b"x" * 1000)
    assert compute_ed2k(str(f)) == compute_ed2k(str(f))


# ── mmap / read parity ──────────────────────────────────────────────


def test_hash_read_and_mmap_parity(tmp_path: Path) -> None:
    """Both paths produce identical digests for a multi-chunk file."""
    f = tmp_path / "parity"
    f.write_bytes(b"\xab" * (ED2K_CHUNK_SIZE + 500))
    total = f.stat().st_size
    assert _hash_read(str(f), total, None) == _hash_mmap(str(f), total, None)


# ── Threshold dispatch ──────────────────────────────────────────────


def test_dispatch_uses_mmap_above_threshold(tmp_path: Path) -> None:
    f = tmp_path / "big"
    data = b"x" * 200
    f.write_bytes(data)

    with patch("anipyrenamer.ed2k.MMAP_THRESHOLD", 100):
        with patch("anipyrenamer.ed2k._hash_mmap", wraps=_hash_mmap) as mock_mmap:
            with patch("anipyrenamer.ed2k._hash_read", wraps=_hash_read) as mock_read:
                compute_ed2k(str(f))
                mock_mmap.assert_called_once()
                mock_read.assert_not_called()


def test_dispatch_uses_read_at_or_below_threshold(tmp_path: Path) -> None:
    f = tmp_path / "small"
    data = b"x" * 100
    f.write_bytes(data)

    with patch("anipyrenamer.ed2k.MMAP_THRESHOLD", 100):
        with patch("anipyrenamer.ed2k._hash_mmap", wraps=_hash_mmap) as mock_mmap:
            with patch("anipyrenamer.ed2k._hash_read", wraps=_hash_read) as mock_read:
                compute_ed2k(str(f))
                mock_read.assert_called_once()
                mock_mmap.assert_not_called()


# ── Fallback on mmap failure ────────────────────────────────────────


def test_fallback_on_mmap_oserror(tmp_path: Path) -> None:
    """When mmap raises OSError the function falls back to _hash_read."""
    f = tmp_path / "fallback"
    f.write_bytes(b"hello")

    with patch("anipyrenamer.ed2k.MMAP_THRESHOLD", 0):
        with patch("anipyrenamer.ed2k._hash_mmap", side_effect=OSError("no mmap")):
            assert compute_ed2k(str(f)) == HELLO_ED2K


def test_fallback_on_mmap_valueerror(tmp_path: Path) -> None:
    """When mmap raises ValueError the function falls back to _hash_read."""
    f = tmp_path / "fallback_ve"
    f.write_bytes(b"hello")

    with patch("anipyrenamer.ed2k.MMAP_THRESHOLD", 0):
        with patch("anipyrenamer.ed2k._hash_mmap", side_effect=ValueError("addr space")):
            assert compute_ed2k(str(f)) == HELLO_ED2K


# ── Progress callback ───────────────────────────────────────────────


def test_progress_callback_read_path(tmp_path: Path) -> None:
    """Read path: callback fires (0, total) then (bytes_read, total) per chunk."""
    f = tmp_path / "progress_read"
    f.write_bytes(b"x" * 100)
    calls: list[tuple[int, int]] = []

    compute_ed2k(str(f), progress_callback=lambda b, t: calls.append((b, t)))
    assert calls[0] == (0, 100)
    assert calls[-1] == (100, 100)


def test_progress_callback_mmap_path(tmp_path: Path) -> None:
    """mmap path: callback fires (0, total) then (end, total) per chunk."""
    f = tmp_path / "progress_mmap"
    f.write_bytes(b"x" * 200)
    calls: list[tuple[int, int]] = []

    with patch("anipyrenamer.ed2k.MMAP_THRESHOLD", 100):
        compute_ed2k(str(f), progress_callback=lambda b, t: calls.append((b, t)))

    assert calls[0] == (0, 200)
    assert calls[-1] == (200, 200)
    assert len(calls) == 2  # initial (0, 200) + one chunk (200, 200)


def test_mmap_path_keyboard_interrupt_teardown_no_buffer_error(tmp_path: Path) -> None:
    """KeyboardInterrupt during mmap hashing must not raise BufferError on mmap close."""
    f = tmp_path / "big_enough_for_mmap"
    f.write_bytes(b"x" * 200)

    def interrupt_after_first_chunk_progress(bytes_read: int, total: int) -> None:
        if bytes_read > 0:
            raise KeyboardInterrupt

    with patch("anipyrenamer.ed2k.MMAP_THRESHOLD", 100):
        with pytest.raises(KeyboardInterrupt):
            compute_ed2k(str(f), progress_callback=interrupt_after_first_chunk_progress)

    other = tmp_path / "after_interrupt"
    other.write_bytes(b"hello")
    assert compute_ed2k(str(other)) == HELLO_ED2K


# ── MMAP_THRESHOLD constant ─────────────────────────────────────────


def test_mmap_threshold_value() -> None:
    assert MMAP_THRESHOLD == 50 * 1024 * 1024
