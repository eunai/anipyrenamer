"""ED2K hash: single pass, chunk size 9 728 000 bytes, MD4."""

from __future__ import annotations

import os
from typing import Callable

from Crypto.Hash import MD4

ED2K_CHUNK_SIZE = 9_728_000


def compute_ed2k(
    path: str,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> str:
    """
    Compute ED2K hash of file. One read pass; chunk size 9728000 bytes.
    Single chunk -> that MD4 hex (uppercase); multiple -> MD4 of concatenated chunk hashes.
    progress_callback(bytes_read, total_bytes) is called at start and after each chunk for live UI.
    """
    total = os.path.getsize(path)
    if progress_callback is not None:
        progress_callback(0, total)
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
    if not hashes:
        return MD4.new(b"").hexdigest().upper()
    if len(hashes) == 1:
        return hashes[0].hex().upper()
    combined = b"".join(hashes)
    return MD4.new(combined).hexdigest().upper()
