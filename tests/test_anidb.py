"""Tests for AniDB client (parsing and throttle, no live UDP)."""

from __future__ import annotations

import socket

import pytest

from anipyrenamer.anidb import (
    AniDBClient,
    MAX_FIELD_LENGTH,
    AniDBConfig,
    _UDP_RECV_BUFFER,
    _looks_like_hash,
    _parse_file_response,
    _redact,
    _safe_int,
    _sanitize_field,
)


def test_redact_masks_pass() -> None:
    assert _redact("pass=secret") == "pass=***"
    assert "secret" not in _redact("prefix pass=secret&suffix")


def test_redact_masks_session_s() -> None:
    assert _redact("s=ABCDEF123") == "s=***"
    assert "ABCDEF123" not in _redact("FILE ... s=ABCDEF123")


def test_redact_masks_pass_and_s_in_one_message() -> None:
    out = _redact("AUTH pass=mysecret&s=SESSKEY99")
    assert out == "AUTH pass=***&s=***"
    assert "mysecret" not in out and "SESSKEY99" not in out


def test_redact_masks_encrypt_salt() -> None:
    out = _redact("209 SALT123 ENCRYPTION ENABLED")
    assert "SALT123" not in out
    assert "209 *** ENCRYPTION ENABLED" in out


def test_redact_masks_api_key_value() -> None:
    """ENCRYPT slice + follow-up review: api_key= must be redacted alongside pass= / s=."""
    out = _redact("ENCRYPT api_key=DEADBEEF&pass=secret&s=SESS123")
    assert "DEADBEEF" not in out
    assert "secret" not in out
    assert "SESS123" not in out
    assert "api_key=***" in out
    assert "pass=***" in out
    assert "s=***" in out


def test_send_recv_debug_prints_redacted_inbound_and_outbound(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-04: inbound preview must not leak session key in debug output."""

    class FakeSock:
        def sendto(self, payload: bytes, addr: tuple[str, int]) -> None:  # noqa: ARG002
            return None

        def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:  # noqa: ARG002
            return (
                b"500 BANNED s=SHOULD_NOT_LEAK&reason=test",
                ("api.anidb.net", 9000),
            )

    cfg = AniDBConfig("u", "p", "c", "1", 0)
    client = AniDBClient(cfg, debug=True)
    monkeypatch.setattr(client, "_ensure_socket", lambda: FakeSock())
    monkeypatch.setattr(client, "_throttle", lambda: None)

    client._send_recv("FILE s=OUTBOUND_SESSION&ed2k=x")
    captured = capsys.readouterr().out
    assert "OUTBOUND_SESSION" not in captured
    assert "SHOULD_NOT_LEAK" not in captured
    assert "s=***" in captured


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


def test_config_from_env_includes_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIDB_API_KEY", "k")
    cfg = AniDBConfig.from_env()
    assert cfg.api_key == "k"


def test_udp_recv_buffer_constant() -> None:
    assert _UDP_RECV_BUFFER == 65535


def test_send_recv_truncation_warning_when_full_buffer(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-07: warn on full recv buffer even when debug is off (no payload echoed)."""

    class FakeSock:
        def sendto(self, payload: bytes, addr: tuple[str, int]) -> None:  # noqa: ARG002
            return None

        def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
            assert size == _UDP_RECV_BUFFER
            return (b"x" * _UDP_RECV_BUFFER, ("api.anidb.net", 9000))

    cfg = AniDBConfig("u", "p", "c", "1", 0)
    client = AniDBClient(cfg, debug=False)
    monkeypatch.setattr(client, "_ensure_socket", lambda: FakeSock())
    monkeypatch.setattr(client, "_throttle", lambda: None)

    client._send_recv("PING")
    out = capsys.readouterr().out
    assert "WARNING: received data may be truncated" in out


def test_safe_int_malformed_and_valid(capsys: pytest.CaptureFixture[str]) -> None:
    assert _safe_int("abc") == 0
    assert "invalid integer" in capsys.readouterr().out.lower()
    assert _safe_int("123") == 123
    assert _safe_int("") == 0
    assert _safe_int("  ") == 0


def test_sanitize_field_strips_controls_and_truncates() -> None:
    assert _sanitize_field("a\x01b") == "ab"
    long_s = "x" * (MAX_FIELD_LENGTH + 50)
    assert len(_sanitize_field(long_s)) == MAX_FIELD_LENGTH


def test_parse_file_response_malformed_int_fields_does_not_crash() -> None:
    ed2k = "e" * 32
    line = f"bad|bad|bad|bad|0||0|1|999|{ed2k}"
    info = _parse_file_response(line, size=999, ed2k=ed2k)
    assert info.fid == 0 and info.aid == 0 and info.eid == 0 and info.gid == 0


def test_parse_file_response_sanitizes_heuristic_title() -> None:
    """SEC-06: heuristic-assigned anime_title must not retain control characters."""
    ed2k = "e" * 32
    line = f"1|2|3|4|0||0|1|100|{ed2k}|high|DTV|Hello\x01World"
    info = _parse_file_response(line, size=100, ed2k=ed2k)
    assert "\x01" not in info.anime_title
    assert "Hello" in info.anime_title and "World" in info.anime_title


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


def test_encrypt_success_enables_encryption(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = AniDBConfig("u", "p", "c", "1", 0, api_key="k")
    client = AniDBClient(cfg)
    monkeypatch.setattr(client, "_send_recv", lambda _msg: "209 SALT123 ENCRYPTION ENABLED")
    ok, msg = client.encrypt()
    assert ok is True and msg == ""
    assert client.encryption_enabled is True


def test_encrypt_failure_does_not_enable_encryption(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = AniDBConfig("u", "p", "c", "1", 0, api_key="k")
    client = AniDBClient(cfg)
    monkeypatch.setattr(client, "_send_recv", lambda _msg: "309 API PASSWORD NOT DEFINED")
    ok, msg = client.encrypt()
    assert ok is False
    assert "309" in msg
    assert client.encryption_enabled is False


def test_send_recv_encrypts_payload_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force a known key and encryption state without depending on ENCRYPT reply parsing.
    cfg = AniDBConfig("u", "p", "c", "1", 0, api_key="k")
    client = AniDBClient(cfg)
    client._aes_key = b"\x00" * 16
    client._encrypted = True

    class FakeSock:
        sent: bytes | None = None

        def sendto(self, payload: bytes, addr: tuple[str, int]) -> None:  # noqa: ARG002
            self.sent = payload

        def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:  # noqa: ARG002
            # Return encrypted bytes for plaintext "200 OK" using the same key.
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import pad

            cipher = AES.new(b"\x00" * 16, AES.MODE_ECB)
            return cipher.encrypt(pad(b"200 OK", AES.block_size)), ("api.anidb.net", 9000)

    fake = FakeSock()
    monkeypatch.setattr(client, "_ensure_socket", lambda: fake)
    monkeypatch.setattr(client, "_throttle", lambda: None)

    reply = client._send_recv("PING")
    assert reply == "200 OK"
    assert fake.sent is not None
    assert fake.sent != b"PING"


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
