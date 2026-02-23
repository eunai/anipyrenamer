"""ED2K hash: single pass, chunk size 9 728 000 bytes, MD4."""

from __future__ import annotations

from Crypto.Hash import MD4

ED2K_CHUNK_SIZE = 9_728_000


def compute_ed2k(path: str) -> str:
    """
    Compute ED2K hash of file. One read pass; chunk size 9728000 bytes.
    Single chunk -> that MD4 hex (uppercase); multiple -> MD4 of concatenated chunk hashes.
    """
    hashes: list[bytes] = []
    with open(path, "rb") as f:
        while True:
            chunk = f.read(ED2K_CHUNK_SIZE)
            if not chunk:
                break
            hashes.append(MD4.new(chunk).digest())
    if not hashes:
        return MD4.new(b"").hexdigest().upper()
    if len(hashes) == 1:
        return hashes[0].hex().upper()
    combined = b"".join(hashes)
    return MD4.new(combined).hexdigest().upper()
