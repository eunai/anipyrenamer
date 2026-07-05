"""Tests for shared model-adjacent helpers."""

from __future__ import annotations

from anipyrenamer.models import looks_like_hash


def test_looks_like_hash_crc32_like() -> None:
    """8-char hex with letters reads as a CRC32-style hash."""
    assert looks_like_hash("d6be2d15") is True
    assert looks_like_hash("abcdef01") is True


def test_looks_like_hash_digits_only_is_not_a_hash() -> None:
    """8 digits with no hex letters could be a real title/number."""
    assert looks_like_hash("12345678") is False


def test_looks_like_hash_too_short() -> None:
    assert looks_like_hash("ab") is False


def test_looks_like_hash_md5_ed2k_length() -> None:
    """32-char hex reads as an MD5/ED2K-length hash."""
    assert looks_like_hash("e" * 32) is True


def test_looks_like_hash_rejects_non_hex_text() -> None:
    """Ordinary titles are not hashes even at hash-like lengths."""
    assert looks_like_hash("Cowboy Bebop") is False
    assert looks_like_hash("a" * 7 + "z") is False
