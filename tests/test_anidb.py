"""Tests for AniDB client (parsing and throttle, no live UDP)."""
from __future__ import annotations

import pytest

from anipyrenamer.anidb import AniDBConfig, _parse_file_response


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
