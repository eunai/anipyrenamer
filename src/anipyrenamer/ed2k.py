"""ED2K hash: single pass, chunk size 9 728 000 bytes, MD4."""

from __future__ import annotations

import mmap
import os
from typing import Callable

from Crypto.Hash import MD4

ED2K_CHUNK_SIZE = 9_728_000
MMAP_THRESHOLD = 50 * 1024 * 1024  # 50 MB


def _hash_read(
    path: str,
    total: int,
    progress_callback: Callable[[int, int], None] | None,
) -> list[bytes]:
    """Hash file using sequential f.read() calls."""
    hashes: list[bytes] = []
    bytes_read = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(ED2K_CHUNK_SIZE)
            if not chunk:
                break
            hashes.append(MD4.new(chunk).digest())
            bytes_read += len(chunk)
            if progress_callback is not None:
                progress_callback(bytes_read, total)
    return hashes


def _hash_mmap(
    path: str,
    total: int,
    progress_callback: Callable[[int, int], None] | None,
) -> list[bytes]:
    """Hash file using mmap + memoryview slicing (zero-copy per chunk)."""
    hashes: list[bytes] = []
    with open(path, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            view = memoryview(mm)
            try:
                for offset in range(0, total, ED2K_CHUNK_SIZE):
                    end = min(offset + ED2K_CHUNK_SIZE, total)
                    chunk = bytes(view[offset:end])
                    hashes.append(MD4.new(chunk).digest())
                    if progress_callback is not None:
                        progress_callback(end, total)
            finally:
                view.release()
    return hashes


def compute_ed2k(
    path: str,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> str:
    """
    Compute ED2K hash of file. One read pass; chunk size 9728000 bytes.
    Single chunk -> that MD4 hex (uppercase); multiple -> MD4 of concatenated chunk hashes.
    progress_callback(bytes_read, total_bytes) is called at start and after each chunk for live UI.

    Files larger than MMAP_THRESHOLD use memory-mapped I/O for reduced
    syscall overhead; smaller files use sequential reads.  If mmap fails
    the function falls back to sequential reads silently.
    """
    total = os.path.getsize(path)
    if progress_callback is not None:
        progress_callback(0, total)

    if total == 0:
        return MD4.new(b"").hexdigest().upper()

    if total > MMAP_THRESHOLD:
        try:
            hashes = _hash_mmap(path, total, progress_callback)
        except (OSError, ValueError):
            hashes = _hash_read(path, total, progress_callback)
    else:
        hashes = _hash_read(path, total, progress_callback)

    if len(hashes) == 1:
        return hashes[0].hex().upper()
    combined = b"".join(hashes)
    return MD4.new(combined).hexdigest().upper()
