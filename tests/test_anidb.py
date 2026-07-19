"""Tests for AniDB client (parsing and throttle, no live UDP)."""

from __future__ import annotations

import inspect
import socket

import pytest

from anipyrenamer.anidb import (
    AniDBBannedError,
    AniDBClient,
    MAX_FIELD_LENGTH,
    AniDBConfig,
    BURST_SIZE,
    LONG_PACKET_INTERVAL,
    MAX_RETRIES,
    SHORT_PACKET_INTERVAL,
    RETRY_BASE_SECONDS,
    PING_TIMEOUT,
    _UDP_RECV_BUFFER,
    _parse_file_response,
    _redact,
    _safe_int,
    _sanitize_field,
)


def test_ping_reachability_sends_one_plain_throttled_packet() -> None:
    """PING uses one plain packet and the locked timeout."""
    sent: list[bytes] = []
    throttles: list[bool] = []

    class FakeSock:
        def __init__(self) -> None:
            self.timeout = 15.0

        def gettimeout(self) -> float:
            return self.timeout

        def settimeout(self, value: float) -> None:
            self.timeout = value

        def sendto(self, payload: bytes, addr: tuple[str, int]) -> None:  # noqa: ARG002
            sent.append(payload)

        def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:  # noqa: ARG002
            return b"300 PONG", ("api.anidb.net", 9000)

    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    fake = FakeSock()
    client._ensure_socket = lambda: fake  # type: ignore[method-assign]
    client._throttle = lambda: throttles.append(True)  # type: ignore[method-assign]

    outcome = client.ping_reachability()

    assert outcome.status == "reachable"
    assert outcome.reply_code == 300
    assert sent == [b"PING"]
    assert throttles == [True]
    assert fake.timeout == 15.0


def test_ping_reachability_never_encrypts_or_uses_session() -> None:
    """PING remains plain even if a client instance carries protocol state."""
    sent: list[bytes] = []

    class FakeSock:
        def __init__(self) -> None:
            self.timeout = 15.0

        def gettimeout(self) -> float:
            return self.timeout

        def settimeout(self, value: float) -> None:
            self.timeout = value

        def sendto(self, payload: bytes, addr: tuple[str, int]) -> None:  # noqa: ARG002
            sent.append(payload)

        def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:  # noqa: ARG002
            return b"300 PONG", ("api.anidb.net", 9000)

    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0, api_key="secret"))
    client._encrypted = True
    client._aes_key = b"x" * 16
    client._session = "session-secret"
    client._ensure_socket = lambda: FakeSock()  # type: ignore[method-assign]
    client._throttle = lambda: None  # type: ignore[method-assign]
    client._aes_encrypt = lambda _: (_ for _ in ()).throw(AssertionError("encrypted PING"))  # type: ignore[method-assign]

    outcome = client.ping_reachability()

    assert outcome.status == "reachable"
    assert sent == [b"PING"]
    assert client.has_session is True


@pytest.mark.parametrize(
    ("reply", "status", "code"),
    [
        (b"500 BANNED", "unreachable", 500),
        (b"300 NOT PONG", "unreachable", 300),
        (b"not an AniDB reply", "malformed", None),
    ],
)
def test_ping_reachability_classifies_replies(
    reply: bytes,
    status: str,
    code: int | None,
) -> None:
    class FakeSock:
        def __init__(self) -> None:
            self.timeout = 15.0

        def gettimeout(self) -> float:
            return self.timeout

        def settimeout(self, value: float) -> None:
            self.timeout = value

        def sendto(self, payload: bytes, addr: tuple[str, int]) -> None:  # noqa: ARG002
            return None

        def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:  # noqa: ARG002
            return reply, ("api.anidb.net", 9000)

    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._ensure_socket = lambda: FakeSock()  # type: ignore[method-assign]
    client._throttle = lambda: None  # type: ignore[method-assign]

    outcome = client.ping_reachability()

    assert outcome.status == status
    assert outcome.reply_code == code


def test_ping_reachability_classifies_timeout_and_socket_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class TimeoutSock:
        def __init__(self) -> None:
            self.timeout = 15.0

        def gettimeout(self) -> float:
            return self.timeout

        def settimeout(self, value: float) -> None:
            self.timeout = value

        def sendto(self, payload: bytes, addr: tuple[str, int]) -> None:  # noqa: ARG002
            return None

        def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:  # noqa: ARG002
            raise socket.timeout("secret network detail")

    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._ensure_socket = lambda: TimeoutSock()  # type: ignore[method-assign]
    client._throttle = lambda: None  # type: ignore[method-assign]
    assert client.ping_reachability().status == "timeout"
    assert "secret network detail" not in capsys.readouterr().out

    def raise_socket() -> object:
        raise OSError("secret socket detail")

    client._ensure_socket = raise_socket  # type: ignore[method-assign]
    assert client.ping_reachability().status == "socket_error"


def test_ping_timeout_constant() -> None:
    assert PING_TIMEOUT == 3.0


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


def test_parse_file_response_skips_hash_looking_field_as_title() -> None:
    """A hash-looking reply field (e.g. CRC32) is never picked up as anime_title."""
    size = 100
    ed2k = "a" * 32
    line = f"1|2|3|4|0||0|1|{size}|{ed2k}|d6be2d15|Show"
    info = _parse_file_response(line, size=size, ed2k=ed2k)
    assert info.anime_title == "Show"


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


# --- Slice 1: untested-invariant characterization — AniDB constants (SPEC.md §6) ---


def test_throttle_and_retry_constants() -> None:
    """The throttle constants match AniDB's documented flood limits (SPEC.md §6): a short-term
    limit of 1 packet / 2 s and a long-term limit of 1 packet / 4 s over an extended run."""
    assert MAX_RETRIES == 3
    assert RETRY_BASE_SECONDS == 0.5
    assert BURST_SIZE == 5
    assert SHORT_PACKET_INTERVAL == 2.0
    assert LONG_PACKET_INTERVAL == 4.0


def test_socket_timeout_is_15s() -> None:
    """Characterization: the AniDB UDP socket uses a 15s timeout (SPEC.md §6)."""
    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    sock = client._ensure_socket()
    try:
        assert sock.gettimeout() == 15.0
    finally:
        sock.close()


def test_send_recv_once_default_timeout_is_5s() -> None:
    """Characterization: the single send/recv attempt defaults to a 5s timeout (SPEC.md §6)."""
    default = inspect.signature(AniDBClient._send_recv_once).parameters["timeout"].default
    assert default == 5.0


# --- Slice 3: MyList reply-code branch characterization (SPEC.md §6) ---


def test_mylist_add_320_no_such_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Characterization: MYLISTADD 320 -> failure 'No such file on AniDB.'; session kept."""
    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._session = "sess"
    monkeypatch.setattr(client, "_send_recv", lambda _msg: "320 NO SUCH FILE")
    ok, msg = client.mylist_add_or_update_by_fid(22, add_to_mylist=True, state=1)
    assert ok is False
    assert msg == "No such file on AniDB."
    assert client._session == "sess"


def test_mylist_add_506_clears_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Characterization: MYLISTADD 506 -> 'Invalid session.' and the local session is cleared."""
    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._session = "sess"
    monkeypatch.setattr(client, "_send_recv", lambda _msg: "506 INVALID SESSION")
    ok, msg = client.mylist_add_or_update_by_fid(22, add_to_mylist=True, state=1)
    assert ok is False
    assert msg == "Invalid session."
    assert client._session is None


def test_mylist_edit_411_no_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Characterization: edit (via 310 -> MYLISTADD edit=1) 411 -> 'No such MyList entry.'; session kept."""
    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._session = "sess"
    replies = iter(["310 FILE ALREADY IN MYLIST\n77", "411 NO SUCH MYLIST ENTRY"])
    monkeypatch.setattr(client, "_send_recv", lambda _msg: next(replies))
    ok, msg = client.mylist_add_or_update_by_fid(22, add_to_mylist=True, state=2)
    assert ok is False
    assert msg == "No such MyList entry."
    assert client._session == "sess"


def test_mylist_edit_506_clears_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Characterization: edit 506 -> 'Invalid session.' and the local session is cleared."""
    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._session = "sess"
    replies = iter(["310 FILE ALREADY IN MYLIST\n77", "506 INVALID SESSION"])
    monkeypatch.setattr(client, "_send_recv", lambda _msg: next(replies))
    ok, msg = client.mylist_add_or_update_by_fid(22, add_to_mylist=True, state=2)
    assert ok is False
    assert msg == "Invalid session."
    assert client._session is None


def test_mylist_lookup_506_clears_session_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Characterization: MYLIST lookup 506 clears the local session and returns no entry (None)."""
    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._session = "sess"
    monkeypatch.setattr(client, "_send_recv", lambda _msg: "506 INVALID SESSION")
    entry = client.mylist_entry_by_fid(22)
    assert entry is None
    assert client._session is None


# --- Slice 4: MyList update-only path with no existing entry (SPEC.md §6) ---


def test_mylist_update_only_no_existing_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Characterization: update-only (add_to_mylist=False) with no existing entry fails with
    'No existing MyList entry to update.' and never sends a MYLISTADD/add command."""
    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._session = "sess"
    sent: list[str] = []

    def fake_send(msg: str) -> str:
        sent.append(msg)
        return "321 NO SUCH ENTRY"  # MYLIST lookup: not 221 -> no existing entry

    monkeypatch.setattr(client, "_send_recv", fake_send)
    ok, msg = client.mylist_add_or_update_by_fid(22, add_to_mylist=False, state=1)
    assert ok is False
    assert msg == "No existing MyList entry to update."
    # Update-only never takes the add/edit path: only the MYLIST lookup is sent, no MYLISTADD.
    assert sent == ["MYLIST fid=22&s=sess"]
    assert not any("MYLISTADD" in s for s in sent)


# --- Slice 5 (client layer): FILE 506 clears the session; not-found does not (SPEC.md §6) ---


def test_file_lookup_506_clears_session_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Characterization: FILE 506 (invalid session) clears the local session and returns None —
    the caller's signal to re-login and retry."""
    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._session = "sess"
    monkeypatch.setattr(client, "_send_recv", lambda _msg: "506 INVALID SESSION")
    info = client.file_lookup(100, "A" * 32)
    assert info is None
    assert client.has_session is False  # session cleared -> re-login signal


def test_file_lookup_not_found_keeps_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Characterization: an ordinary not-found (320/322/505) returns None but KEEPS the session,
    so it is distinguishable from the 506 invalid-session path."""
    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._session = "sess"
    monkeypatch.setattr(client, "_send_recv", lambda _msg: "320 NO SUCH FILE")
    info = client.file_lookup(100, "A" * 32)
    assert info is None
    assert client.has_session is True  # session intact -> NOT a re-login signal


# --- Slice 6: throttle timing via the injected clock/sleep seam (SPEC.md §6) ---


def test_throttle_enforces_short_then_long_gaps() -> None:
    """The throttle keeps a minimum gap between packets — never a zero-delay burst. The first
    BURST_SIZE packets may use the short-term 2 s spacing; sustained traffic afterward drops to
    the long-term 4 s spacing (AniDB flood policy, SPEC.md §6)."""
    clock = {"t": 0.0}
    sleeps: list[float] = []

    def fake_now() -> float:
        return clock["t"]

    def fake_sleep(secs: float) -> None:
        sleeps.append(secs)
        clock["t"] += secs  # a real sleep advances wall-clock by that much

    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0), now=fake_now, sleep=fake_sleep)

    # First packet has no predecessor: nothing to space against, so no sleep.
    client._throttle()
    assert sleeps == []

    # Warm-up packets (2 .. BURST_SIZE): back-to-back, each spaced by the short-term 2 s limit —
    # not the old zero-delay burst.
    for _ in range(BURST_SIZE - 1):
        client._throttle()
        assert sleeps[-1] == pytest.approx(SHORT_PACKET_INTERVAL)  # 2.0
    assert len(sleeps) == BURST_SIZE - 1

    # Once past the warm-up, sustained traffic uses the long-term 4 s spacing.
    client._throttle()
    assert sleeps[-1] == pytest.approx(LONG_PACKET_INTERVAL)  # 4.0

    # If real work between packets already exceeded the interval, there is NO extra sleep.
    clock["t"] += 100.0
    before = len(sleeps)
    client._throttle()
    assert len(sleeps) == before  # already spaced enough -> no throttle sleep


# --- Issue #59: AniDB 555 BANNED is signalled, not swallowed as a per-file failure ---


def test_mylist_edit_555_raises_banned_with_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 555 on the edit path raises AniDBBannedError carrying AniDB's reason and marks the
    client banned, instead of returning a generic per-file failure the loop would keep going past."""
    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._session = "sess"
    replies = iter(["310 FILE ALREADY IN MYLIST\n77", "555 BANNED\nFlooding"])
    monkeypatch.setattr(client, "_send_recv", lambda _msg: next(replies))

    with pytest.raises(AniDBBannedError) as excinfo:
        client.mylist_add_or_update_by_fid(22, add_to_mylist=True, state=2)

    assert excinfo.value.reason == "Flooding"
    assert client._banned is True


def test_mylist_add_555_raises_banned(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 555 on the direct add path (no prior 310) also raises AniDBBannedError and marks banned."""
    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._session = "sess"
    monkeypatch.setattr(client, "_send_recv", lambda _msg: "555 BANNED\nFlooding")

    with pytest.raises(AniDBBannedError) as excinfo:
        client.mylist_add_or_update_by_fid(22, add_to_mylist=True, state=1)

    assert excinfo.value.reason == "Flooding"
    assert client._banned is True


def test_send_recv_refuses_to_send_once_banned() -> None:
    """Once banned, _send_recv raises AniDBBannedError immediately without touching the socket —
    a ban-induced silence must never be retried as a transient timeout (issue #59)."""
    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._banned = True
    client._ban_reason = "Flooding"

    def _boom() -> object:  # pragma: no cover - must never be reached
        raise AssertionError("banned client must not open a socket")

    client._ensure_socket = _boom  # type: ignore[method-assign]

    with pytest.raises(AniDBBannedError) as excinfo:
        client._send_recv("PING")
    assert excinfo.value.reason == "Flooding"


def test_logout_sends_nothing_once_banned() -> None:
    """The "no further packet after a ban" invariant (issue #59) includes cleanup: a banned
    client's logout() must not send LOGOUT — it closes locally and drops session state."""
    sent: list[bytes] = []
    closed: list[bool] = []

    class FakeSock:
        def gettimeout(self) -> float:
            return 15.0

        def settimeout(self, _t: float) -> None:
            pass

        def sendto(self, payload: bytes, _addr: object) -> int:  # pragma: no cover - must not run
            sent.append(payload)
            return len(payload)

        def recvfrom(self, _n: int) -> tuple[bytes, object]:  # pragma: no cover - must not run
            raise AssertionError("banned client must not recv during logout")

        def close(self) -> None:
            closed.append(True)

    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._session = "sess"
    client._sock = FakeSock()  # type: ignore[assignment]
    client._banned = True
    client._ban_reason = "Flooding"

    client.logout()

    assert sent == []  # zero packets sent during cleanup
    assert closed == [True]  # socket still closed
    assert client._session is None  # local session dropped


def test_mylist_lookup_555_raises_banned(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 555 on the MYLIST lookup (update-only path) raises AniDBBannedError and latches the ban,
    rather than being read as an ordinary non-221 'no entry' response (issue #59)."""
    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._session = "sess"
    monkeypatch.setattr(client, "_send_recv", lambda _msg: "555 BANNED\nFlooding")

    with pytest.raises(AniDBBannedError) as excinfo:
        client.mylist_entry_by_fid(22)

    assert excinfo.value.reason == "Flooding"
    assert client._banned is True


def test_mylist_update_only_555_raises_banned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Update-only mode (add_to_mylist=False) surfaces a lookup 555 as a ban, not as
    'No existing MyList entry to update.' — so the wizard aborts instead of continuing."""
    client = AniDBClient(AniDBConfig("u", "p", "c", "1", 0))
    client._session = "sess"
    monkeypatch.setattr(client, "_send_recv", lambda _msg: "555 BANNED\nFlooding")

    with pytest.raises(AniDBBannedError):
        client.mylist_add_or_update_by_fid(22, add_to_mylist=False, state=1)
