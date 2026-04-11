"""Tests for AniDB client (parsing and throttle, no live UDP)."""

from __future__ import annotations

import socket

import pytest

from anipyrenamer.anidb import AniDBClient, AniDBConfig, _looks_like_hash, _parse_file_response


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIDB_USERNAME", "u")
    monkeypatch.setenv("ANIDB_PASSWORD", "p")
    monkeypatch.setenv("ANIDB_UDP_CLIENT", "testclient")
    monkeypatch.setenv("ANIDB_UDP_CLIENTVER", "2")
    monkeypatch.setenv("ANIDB_LOCAL_PORT", "9876")
    cfg = AniDBConfig.from_env()
    assert cfg.username == "u"
    assert cfg.password == "p"
    assert cfg.client == "testclient"
    assert cfg.clientver == "2"
    assert cfg.local_port == 9876


def test_parse_file_response_minimal() -> None:
    line = "100|200|300|400|0||0|1|12345|abcdef0123456789abcdef0123456789"
    info = _parse_file_response(line, size=12345, ed2k="abcdef0123456789abcdef0123456789")
    assert info.fid == 100
    assert info.aid == 200
    assert info.eid == 300
    assert info.gid == 400
    assert info.size == 12345
    assert info.ed2k == "abcdef0123456789abcdef0123456789"


def test_parse_file_response_with_quality_source() -> None:
    ed2k = "e" * 32
    size = 999
    line = f"1|2|3|4|0||0|1|{size}|{ed2k}|high|DTV"
    info = _parse_file_response(line, size=size, ed2k=ed2k)
    assert info.quality == "high"
    assert info.source == "DTV"


def test_looks_like_hash_crc32_not_used_as_title() -> None:
    """8-char hex (CRC32) must be treated as hash so FILE parser does not use it as anime_title."""
    assert _looks_like_hash("d6be2d15") is True
    assert _looks_like_hash("abcdef01") is True
    assert _looks_like_hash("12345678") is False  # digits only, no a-f
    assert _looks_like_hash("ab") is False  # too short
    assert _looks_like_hash("e" * 32) is True  # MD5/ED2K length


def test_send_recv_retries_transient_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """_send_recv retries on timeout and eventually returns reply."""

    class FakeSock:
        def __init__(self) -> None:
            self.calls = 0

        def sendto(self, payload: bytes, addr: tuple[str, int]) -> None:  # noqa: ARG002
            return None

        def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:  # noqa: ARG002
            self.calls += 1
            if self.calls == 1:
                raise socket.timeout("timed out")
            return b"200 SESSION LOGIN ACCEPTED", ("api.anidb.net", 9000)

    cfg = AniDBConfig("u", "p", "c", "1", 0)
    client = AniDBClient(cfg)
    fake = FakeSock()
    monkeypatch.setattr(client, "_ensure_socket", lambda: fake)
    monkeypatch.setattr(client, "_throttle", lambda: None)
    monkeypatch.setattr("anipyrenamer.anidb.time.sleep", lambda _: None)
    monkeypatch.setattr("anipyrenamer.anidb.random.uniform", lambda a, b: 0.0)

    reply = client._send_recv("PING")
    assert "200 SESSION" in reply


def test_mylist_entry_by_fid_parses_221_response(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = AniDBConfig("u", "p", "c", "1", 0)
    client = AniDBClient(cfg)
    client._session = "sess"

    monkeypatch.setattr(
        client,
        "_send_recv",
        lambda _msg: "221 MYLIST\n11|22|33|44|55|100|1|200|Internal HDD|src|other|0",
    )
    entry = client.mylist_entry_by_fid(22)
    assert entry is not None
    assert entry.lid == 11
    assert entry.fid == 22
    assert entry.state == 1
    assert entry.storage == "Internal HDD"


def test_mylist_add_or_update_by_fid_add_success(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = AniDBConfig("u", "p", "c", "1", 0)
    client = AniDBClient(cfg)
    client._session = "sess"

    monkeypatch.setattr(client, "_send_recv", lambda _msg: "210 MYLIST ENTRY ADDED\n99")
    ok, msg = client.mylist_add_or_update_by_fid(
        22,
        add_to_mylist=True,
        state=1,
        storage="Internal HDD",
        viewed=True,
    )
    assert ok is True
    assert "Added" in msg


def test_mylist_add_or_update_by_fid_existing_entry_then_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = AniDBConfig("u", "p", "c", "1", 0)
    client = AniDBClient(cfg)
    client._session = "sess"

    replies = iter(
        [
            "310 FILE ALREADY IN MYLIST\n11|22|33|44|55|100|1|200|Internal HDD|src|other|0",
            "311 MYLIST ENTRY EDITED",
        ]
    )
    monkeypatch.setattr(client, "_send_recv", lambda _msg: next(replies))
    ok, msg = client.mylist_add_or_update_by_fid(
        22,
        add_to_mylist=True,
        state=2,
        storage="External CD/DVD",
        viewed=False,
    )
    assert ok is True
    assert "updated" in msg.lower()
