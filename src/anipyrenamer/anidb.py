"""AniDB UDP client: AUTH, FILE, throttle (burst 5, then 1 per 2.5s)."""

from __future__ import annotations

import logging
import os
import random
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import hashlib

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from anipyrenamer.models import FileInfo, looks_like_hash

_LOGGER = logging.getLogger("anipyrenamer.anidb")

ANIDB_HOST = "api.anidb.net"
ANIDB_PORT = 9000
FILE_FMASK = "79FAFFE900"
FILE_AMASK = "F2FCF0C0"
# Throttle: first 5 packets, then 1 packet per 2.5 s
BURST_SIZE = 5
PACKET_INTERVAL = 2.5
MAX_RETRIES = 3
RETRY_BASE_SECONDS = 0.5
PING_TIMEOUT = 3.0

_UDP_RECV_BUFFER = 65535
MAX_FIELD_LENGTH = 200
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

PingStatus = Literal["reachable", "unreachable", "malformed", "timeout", "socket_error"]


@dataclass(frozen=True)
class PingOutcome:
    """Bounded result from the unauthenticated transport reachability probe."""

    status: PingStatus
    reply_code: int | None = None


def _redact(msg: str) -> str:
    """Mask sensitive values in UDP messages for debug output (pass=, session s=)."""
    # Stop at `&` so `pass=secret&s=sess` redacts both fields (not one greedy \\S+ span).
    msg = re.sub(r"(pass|s|api_key)=[^&\s]+", r"\1=***", msg)
    # Defensive: hide ENCRYPT salt in preview logs.
    msg = re.sub(r"(\b209\s+)\S+(\s+ENCRYPTION ENABLED\b)", r"\1***\2", msg)
    return msg


def _safe_int(raw: str, *, field_name: str = "field") -> int:
    """Parse AniDB integer field; return 0 on empty/malformed without crashing."""
    s = (raw or "").strip()
    if not s:
        return 0
    try:
        return int(s)
    except ValueError:
        print(f"[anidb] WARNING: invalid integer in AniDB response ({field_name})")
        return 0


def _sanitize_field(s: str) -> str:
    """Strip control characters and truncate untrusted AniDB strings before path use."""
    if not s:
        return ""
    out = _CONTROL_CHARS.sub("", s).strip()
    if len(out) > MAX_FIELD_LENGTH:
        out = out[:MAX_FIELD_LENGTH]
    return out


@dataclass
class MyListEntry:
    """Subset of AniDB MYLIST response fields used by Phase 2 wizard."""

    lid: int
    fid: int
    eid: int
    aid: int
    gid: int
    date: int
    state: int
    viewdate: int
    storage: str
    source: str
    other: str
    filestate: int


@dataclass
class AniDBConfig:
    """Credentials and client info from env or args."""

    username: str
    password: str
    client: str
    clientver: str
    local_port: int
    api_key: str = ""

    @classmethod
    def from_env(cls) -> AniDBConfig:
        """Load from environment (e.g. .env via python-dotenv)."""
        return cls(
            username=os.environ.get("ANIDB_USERNAME", ""),
            password=os.environ.get("ANIDB_PASSWORD", ""),
            client=os.environ.get("ANIDB_UDP_CLIENT", "anipyrenamer"),
            clientver=os.environ.get("ANIDB_UDP_CLIENTVER", "1"),
            local_port=int(os.environ.get("ANIDB_LOCAL_PORT", "0") or "0"),
            api_key=os.environ.get("ANIDB_API_KEY", ""),
        )


def _extract_reply_code(reply: str) -> int | None:
    """Extract numeric AniDB reply code from first line (supports tagged replies)."""
    first = reply.split("\n", 1)[0].strip()
    match = re.search(r"\b(\d{3})\b", first)
    if not match:
        return None
    return int(match.group(1))


class AniDBClient:
    """UDP client with throttle: burst 5, then 1 per 2.5s."""

    def __init__(
        self,
        config: AniDBConfig,
        *,
        debug: bool = False,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._debug = debug
        self._now = now
        self._sleep = sleep
        self._sock: socket.socket | None = None
        self._session: str | None = None
        self._packets_sent = 0
        self._burst_start: float = 0.0
        self._encrypted = False
        self._aes_key: bytes | None = None

    @property
    def encryption_enabled(self) -> bool:
        return self._encrypted and self._aes_key is not None

    @property
    def has_session(self) -> bool:
        """True if logged in (session key present)."""
        return self._session is not None

    def _derive_aes_key(self, salt: str) -> bytes:
        """AniDB ENCRYPT key derivation: MD5(api_key + salt) -> 16 bytes."""
        raw = (self._config.api_key or "") + salt
        # FIPS-mode builds may reject MD5 unless explicitly marked not-for-security.
        try:
            return hashlib.md5(raw.encode("utf-8"), usedforsecurity=False).digest()
        except TypeError:
            return hashlib.md5(raw.encode("utf-8")).digest()

    def _aes_encrypt(self, plaintext: bytes) -> bytes:
        key = self._aes_key
        if key is None:
            raise RuntimeError("AES key is not set")
        cipher = AES.new(key, AES.MODE_ECB)
        return cipher.encrypt(pad(plaintext, AES.block_size))

    def _aes_decrypt(self, ciphertext: bytes) -> bytes:
        key = self._aes_key
        if key is None:
            raise RuntimeError("AES key is not set")
        cipher = AES.new(key, AES.MODE_ECB)
        return unpad(cipher.decrypt(ciphertext), AES.block_size)

    def encrypt(self) -> tuple[bool, str]:
        """Establish AES-128 encryption via ENCRYPT (type=1).

        Returns (True, '') on success, else (False, reply/error string). This method does not log in.
        """
        if not self._config.api_key:
            return (False, "ANIDB_API_KEY not set")
        # ENCRYPT is sent unencrypted; only subsequent packets are encrypted.
        msg = f"ENCRYPT user={self._config.username}&type=1"
        reply = self._send_recv(msg)
        m = re.match(r"(?:\S+\s+)?209\s+(\S+)\s+ENCRYPTION ENABLED", reply)
        if not m:
            self._encrypted = False
            self._aes_key = None
            return (False, reply)
        salt = m.group(1)
        self._aes_key = self._derive_aes_key(salt)
        self._encrypted = True
        return (True, "")

    def disable_encryption(self) -> None:
        """Explicitly return to unencrypted mode (used on ENCRYPT failure fallback)."""
        self._encrypted = False
        self._aes_key = None

    def _ensure_socket(self) -> socket.socket:
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if self._config.local_port > 0:
                self._sock.bind(("", self._config.local_port))
            self._sock.settimeout(15.0)
        return self._sock

    def _throttle(self) -> None:
        now = self._now()
        if self._packets_sent == 0:
            self._burst_start = now
        self._packets_sent += 1
        if self._packets_sent <= BURST_SIZE:
            return
        # After burst: wait so we don't exceed 1 per 2.5s
        elapsed = now - self._burst_start
        required = (self._packets_sent - BURST_SIZE) * PACKET_INTERVAL
        if required > elapsed:
            self._sleep(required - elapsed)

    def _send_recv(self, msg: str) -> str:
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._throttle()
                sock = self._ensure_socket()
                if self._debug:
                    print(f"[anidb] >>> {_redact(msg)}")
                payload = msg.encode("utf-8")
                if self.encryption_enabled:
                    payload = self._aes_encrypt(payload)
                sock.sendto(payload, (ANIDB_HOST, ANIDB_PORT))
                data, _ = sock.recvfrom(_UDP_RECV_BUFFER)
                if len(data) == _UDP_RECV_BUFFER:
                    print("[anidb] WARNING: received data may be truncated")
                if self.encryption_enabled:
                    try:
                        data = self._aes_decrypt(data)
                    except Exception as exc:  # noqa: BLE001
                        raise TimeoutError("AniDB encrypted reply could not be decrypted") from exc
                reply = data.decode("utf-8", errors="replace").strip()
                if self._debug:
                    preview = reply[:500] + "..." if len(reply) > 500 else reply
                    print(f"[anidb] <<< {_redact(preview)}")
                return reply
            except (socket.timeout, OSError) as exc:
                last_error = exc
                if attempt >= MAX_RETRIES:
                    break
                # Jittered backoff for transient UDP/network failures.
                backoff = RETRY_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0.0, 0.35)
                time.sleep(backoff)

        raise TimeoutError(
            f"AniDB request failed after {MAX_RETRIES} attempts due to timeout/network issues."
        ) from last_error

    def login(self) -> tuple[bool, str]:
        """AUTH; store session key. Returns (True, '') if 200/201, else (False, reply)."""
        if self._config.api_key and not self.encryption_enabled:
            ok, msg = self.encrypt()
            if not ok:
                # Fall back to unencrypted AUTH; caller/CLI is responsible for warning the user.
                self.disable_encryption()
        cfg = self._config
        msg = (
            f"AUTH user={cfg.username}&pass={cfg.password}"
            f"&protover=3&client={cfg.client}&clientver={cfg.clientver}"
        )
        reply = self._send_recv(msg)
        # 200 session LOGIN ACCEPTED or 201 session LOGIN ACCEPTED - NEW VERSION
        m = re.match(r"(?:\S+\s+)?(?:200|201)\s+(\S+)\s+LOGIN", reply)
        if m:
            self._session = m.group(1)
            code = _extract_reply_code(reply)
            _LOGGER.info(
                "op=AUTH reply_code=%s outcome=accepted",
                code if code is not None else "unknown",
            )
            return (True, "")
        code = _extract_reply_code(reply)
        _LOGGER.info(
            "op=AUTH reply_code=%s outcome=rejected",
            code if code is not None else "unknown",
        )
        return (False, reply)

    def _send_recv_once(self, msg: str, *, timeout: float = 5.0) -> str | None:
        """Single send/recv attempt with a short timeout. Returns reply or None on failure."""
        sock = self._ensure_socket()
        prev = sock.gettimeout()
        try:
            sock.settimeout(timeout)
            self._throttle()
            payload = msg.encode("utf-8")
            if self.encryption_enabled:
                payload = self._aes_encrypt(payload)
            sock.sendto(payload, (ANIDB_HOST, ANIDB_PORT))
            # NOTE: If adding debug output here, use _redact() (see SEC-04).
            data, _ = sock.recvfrom(_UDP_RECV_BUFFER)
            if len(data) == _UDP_RECV_BUFFER:
                print("[anidb] WARNING: received data may be truncated")
            if self.encryption_enabled:
                data = self._aes_decrypt(data)
            return data.decode("utf-8", errors="replace").strip()
        except Exception:
            return None
        finally:
            try:
                sock.settimeout(prev)
            except Exception:
                pass

    def ping_reachability(self) -> PingOutcome:
        """Send one plain PING and classify the bounded transport outcome.

        This seam intentionally bypasses the authenticated/encrypted request path. It does not
        inspect or mutate session state and never retries the packet.
        """
        sock: socket.socket | None = None
        previous_timeout: float | None = None
        try:
            sock = self._ensure_socket()
            previous_timeout = sock.gettimeout()
            sock.settimeout(PING_TIMEOUT)
            self._throttle()
            sock.sendto(b"PING", (ANIDB_HOST, ANIDB_PORT))
            data, _ = sock.recvfrom(_UDP_RECV_BUFFER)
            reply = data.decode("utf-8", errors="replace").strip()
            first_line = reply.split("\n", 1)[0].strip()
            code = _extract_reply_code(reply)
            tokens = first_line.upper().split()
            code_index = next(
                (
                    index
                    for index, token in enumerate(tokens)
                    if token.isdigit() and len(token) == 3
                ),
                None,
            )
            if (
                code == 300
                and code_index is not None
                and tokens[code_index + 1 : code_index + 2] == ["PONG"]
            ):
                return PingOutcome("reachable", code)
            if code is None:
                return PingOutcome("malformed")
            return PingOutcome("unreachable", code)
        except socket.timeout:
            return PingOutcome("timeout")
        except OSError:
            return PingOutcome("socket_error")
        finally:
            if sock is not None and previous_timeout is not None:
                try:
                    sock.settimeout(previous_timeout)
                except OSError:
                    pass

    def close(self) -> None:
        """Close the UDP socket without sending a protocol packet."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def logout(self) -> None:
        """LOGOUT and close socket. Single lightweight attempt (no full retry loop)
        so the server reliably sees LOGOUT without excessive UDP traffic."""
        if self._session and self._sock:
            self._send_recv_once(f"LOGOUT s={self._session}", timeout=5.0)
            self._session = None
        self.close()

    def file_lookup(self, size: int, ed2k: str) -> FileInfo | None:
        """
        FILE size= & ed2k=; on 220 parse to FileInfo; then ANIME/EP/GROUP for names.
        320/322/505 = no file; 506 = re-login and retry (caller can retry).
        """
        if not self._session:
            return None
        msg = (
            f"FILE size={size}&ed2k={ed2k}&fmask={FILE_FMASK}&amask={FILE_AMASK}&s={self._session}"
        )
        reply = self._send_recv(msg)
        lines = reply.split("\n")
        first = lines[0].strip()
        code = _extract_reply_code(first)
        if "220 FILE" in first:
            outcome = "success"
        elif "506" in first:
            outcome = "session_invalid"
        else:
            outcome = "no_file_or_error"
        _LOGGER.info(
            "op=FILE reply_code=%s outcome=%s",
            code if code is not None else "unknown",
            outcome,
        )
        if "220 FILE" not in first:
            if "506" in first:
                self._session = None  # Invalid session; caller should re-login and retry
            return None  # 320, 322, 505, etc.
        if len(lines) < 2:
            return None
        info = _parse_file_response(lines[1].strip(), size=size, ed2k=ed2k)
        if info.aid:
            self._fill_anime_info(info)
        if info.eid:
            self._fill_episode_info(info)
        if info.gid:
            short, long_name = self._group_names(info.gid)
            if short or long_name:
                info.group_short_name = short or long_name
                info.group_name = long_name or short
        return info

    def mylist_entry_by_fid(self, fid: int) -> MyListEntry | None:
        """Return MyList entry for a file id, or None when not found."""
        if not self._session:
            return None
        reply = self._send_recv(f"MYLIST fid={fid}&s={self._session}")
        code = _extract_reply_code(reply)
        if code == 506:
            self._session = None
            return None
        if code != 221:
            return None
        lines = reply.split("\n")
        if len(lines) < 2:
            return None
        fields = [p.strip() for p in lines[1].split("|")]
        if len(fields) < 12:
            return None
        return MyListEntry(
            lid=_safe_int(fields[0], field_name="lid"),
            fid=_safe_int(fields[1], field_name="fid"),
            eid=_safe_int(fields[2], field_name="eid"),
            aid=_safe_int(fields[3], field_name="aid"),
            gid=_safe_int(fields[4], field_name="gid"),
            date=_safe_int(fields[5], field_name="date"),
            state=_safe_int(fields[6], field_name="state"),
            viewdate=_safe_int(fields[7], field_name="viewdate"),
            storage=_sanitize_field(fields[8] or ""),
            source=_sanitize_field(fields[9] or ""),
            other=_sanitize_field(fields[10] or ""),
            filestate=_safe_int(fields[11], field_name="filestate"),
        )

    def mylist_add_or_update_by_fid(
        self,
        fid: int,
        *,
        add_to_mylist: bool,
        state: int | None = None,
        storage: str | None = None,
        viewed: bool | None = None,
    ) -> tuple[bool, str]:
        """
        Add/update a MyList entry by fid.

        Returns (success, status_message).
        """
        if not self._session:
            return (False, "No active AniDB session.")

        update_requested = state is not None or storage is not None or viewed is not None
        if add_to_mylist:
            params = [f"MYLISTADD fid={fid}", f"s={self._session}"]
            if state is not None:
                params.append(f"state={state}")
            if storage:
                params.append(f"storage={storage}")
            if viewed is not None:
                params.append(f"viewed={1 if viewed else 0}")
                if viewed:
                    params.append(f"viewdate={int(time.time())}")
            reply = self._send_recv("&".join(params))
            code = _extract_reply_code(reply)
            if code == 506:
                self._session = None
                return (False, "Invalid session.")
            if code == 210:
                return (True, "Added to MyList.")
            if code == 310:
                # Already in MyList; if updates are requested, edit existing lid.
                if not update_requested:
                    return (True, "Already in MyList.")
                lines = reply.split("\n")
                if len(lines) < 2:
                    return (False, "Already in MyList, but could not parse entry id for update.")
                lid_field = lines[1].split("|", 1)[0].strip()
                lid = _safe_int(lid_field, field_name="lid")
                if lid <= 0:
                    return (False, "Already in MyList, but could not parse entry id for update.")
                return self._edit_mylist_entry(
                    lid=lid,
                    state=state,
                    storage=storage,
                    viewed=viewed,
                )
            if code == 320:
                return (False, "No such file on AniDB.")
            return (False, f"AniDB MYLISTADD failed ({code}).")

        # Update-only mode (do not add if missing)
        if not update_requested:
            return (True, "No MyList changes requested.")
        entry = self.mylist_entry_by_fid(fid)
        if entry is None or entry.lid <= 0:
            return (False, "No existing MyList entry to update.")
        return self._edit_mylist_entry(
            lid=entry.lid,
            state=state,
            storage=storage,
            viewed=viewed,
        )

    def _edit_mylist_entry(
        self,
        *,
        lid: int,
        state: int | None,
        storage: str | None,
        viewed: bool | None,
    ) -> tuple[bool, str]:
        """Edit an existing MyList entry by lid."""
        if not self._session:
            return (False, "No active AniDB session.")
        params = [f"MYLISTADD lid={lid}", "edit=1", f"s={self._session}"]
        if state is not None:
            params.append(f"state={state}")
        if storage:
            params.append(f"storage={storage}")
        if viewed is not None:
            params.append(f"viewed={1 if viewed else 0}")
            if viewed:
                params.append(f"viewdate={int(time.time())}")
        reply = self._send_recv("&".join(params))
        code = _extract_reply_code(reply)
        if code == 506:
            self._session = None
            return (False, "Invalid session.")
        if code == 311:
            return (True, "MyList entry updated.")
        if code == 411:
            return (False, "No such MyList entry.")
        return (False, f"AniDB MYLIST edit failed ({code}).")

    def _fill_anime_info(self, info: FileInfo) -> None:
        """ANIME aid=; fill title variants, year, type, categories, ep_count."""
        if not self._session or not info.aid:
            return
        reply = self._send_recv(f"ANIME aid={info.aid}&s={self._session}")
        if "230 ANIME" not in reply or "\n" not in reply:
            return
        parts = reply.split("\n")[1].strip().split("|")
        # aid|eps|ep count|special cnt|rating|votes|...|year|type|romaji|kanji|english|other|short names|synonyms|category list
        if len(parts) >= 19:
            info.ep_count = _sanitize_field(parts[2] or "")
            info.ep_highest = _sanitize_field(parts[1] or "")
            info.year_begin = _sanitize_field(parts[10] or "")
            info.year_end = info.year_begin  # single year in default format
            info.anime_type = _sanitize_field(parts[11] or "")
            info.title_romaji = _sanitize_field(parts[12] or "")
            info.title_kanji = _sanitize_field(parts[13] or "")
            info.title_english = _sanitize_field(parts[14] or "")
            info.title_other = _sanitize_field(parts[15] or "")
            info.title_synonym = _sanitize_field(parts[17] or "")
            info.categories = _sanitize_field(parts[18] or "")
        if len(parts) >= 15:
            info.title_romaji = info.title_romaji or _sanitize_field(parts[12] or "")
            info.title_english = info.title_english or _sanitize_field(parts[14] or "")
        # Prefer ANIME titles over FILE line heuristic (which can misparse quality/codec as title)
        anime_from_anime = info.title_english or info.title_romaji or info.title_kanji or ""
        if anime_from_anime:
            info.anime_title = _sanitize_field(anime_from_anime)
        elif not info.anime_title:
            info.anime_title = ""

    def _fill_episode_info(self, info: FileInfo) -> None:
        """EPISODE eid=; fill epno, episode title variants."""
        if not self._session or not info.eid:
            return
        reply = self._send_recv(f"EPISODE eid={info.eid}&s={self._session}")
        if "240 EPISODE" not in reply or "\n" not in reply:
            return
        parts = reply.split("\n")[1].strip().split("|")
        # eid|aid|length|rating|votes|epno|eng|romaji|kanji|aired|type
        if len(parts) >= 9:
            # Prefer EPISODE data over FILE heuristic (which can misparse codec/bitrate as epno/eptitle)
            epno = _sanitize_field(parts[5] or "")
            if epno:
                info.episode_number = epno
            info.eptitle_english = _sanitize_field(parts[6] or "")
            info.eptitle_romaji = _sanitize_field(parts[7] or "")
            info.eptitle_kanji = _sanitize_field(parts[8] or "")
        if len(parts) >= 7:
            eptitle = _sanitize_field(parts[6] or "")
            if eptitle:
                info.episode_title = eptitle
        if not info.episode_title and info.eptitle_english:
            info.episode_title = _sanitize_field(info.eptitle_english)

    def _group_names(self, gid: int) -> tuple[str, str]:
        """GROUP gid=; return (short_name, long_name). 250 GROUP: gid|...|name|short|..."""
        if not self._session or gid <= 0:
            return ("", "")
        reply = self._send_recv(f"GROUP gid={gid}&s={self._session}")
        if "250 GROUP" not in reply:
            return ("", "")
        lines = reply.split("\n")
        if len(lines) < 2:
            return ("", "")
        parts = lines[1].strip().split("|")
        # name=parts[5], short=parts[6]
        long_name = _sanitize_field((parts[5] or "").strip() if len(parts) > 5 else "")
        short_name = _sanitize_field((parts[6] or "").strip() if len(parts) > 6 else "")
        return (short_name, long_name)

    def __enter__(self) -> AniDBClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.logout()


def _parse_file_response(data_line: str, size: int, ed2k: str) -> FileInfo:
    """Parse one line of 220 FILE response. We know size and ed2k from request."""
    parts = [p.strip() for p in data_line.split("|")]
    # fid, aid, eid, gid are first four per spec
    fid = _safe_int(parts[0], field_name="fid") if len(parts) > 0 else 0
    aid = _safe_int(parts[1], field_name="aid") if len(parts) > 1 else 0
    eid = _safe_int(parts[2], field_name="eid") if len(parts) > 2 else 0
    gid = _safe_int(parts[3], field_name="gid") if len(parts) > 3 else 0
    quality = ""
    source = ""
    anime_title = ""
    episode_number = ""
    episode_title = ""
    group_name = ""
    file_version = ""
    # Scan for common string fields (quality, source, names). Skip hash-like fields (MD5, SHA1, etc.).
    for i, p in enumerate(parts):
        if p in ("high", "medium", "low", "corrupt", "very high", "backup", "unknown"):
            quality = _sanitize_field(p)
        elif p in ("TV", "DTV", "DVD", "VHS", "HDTV", "LD", "WEB", "Blu-ray", "Blu-Ray"):
            source = _sanitize_field(p)
        elif looks_like_hash(p):
            continue  # do not use hash as title/group
        elif i >= 4 and len(p) > 2 and not p.isdigit() and "|" not in p:
            if not anime_title and p:
                anime_title = _sanitize_field(p)
            elif anime_title and not episode_number and re.match(r"^\d+", p):
                episode_number = _sanitize_field(p)
            elif episode_number and not episode_title and p and p != episode_number:
                episode_title = _sanitize_field(p)
            elif not group_name and p and p != episode_title:
                group_name = _sanitize_field(p)
    # Prefer extracting by position if we have enough fields; otherwise use defaults
    if len(parts) > 10:
        quality = quality or _sanitize_field(parts[10] if parts[10] else "")
        source = source or _sanitize_field(parts[11] if len(parts) > 11 else "")
    # Deliberate: _sanitize_field() is applied both during heuristic assignment
    # above and again here.  The function is idempotent, so the second pass is a
    # defence-in-depth layer ensuring no unsanitised AniDB string reaches
    # FileInfo regardless of future parser changes (SEC-06 / P2-C).
    return FileInfo(
        fid=fid,
        aid=aid,
        eid=eid,
        gid=gid,
        size=size,
        ed2k=ed2k,
        quality=_sanitize_field(quality),
        source=_sanitize_field(source),
        group_name=_sanitize_field(group_name),
        group_short_name="",
        anime_title=_sanitize_field(anime_title),
        episode_number=_sanitize_field(episode_number),
        episode_title=_sanitize_field(episode_title),
        file_version=_sanitize_field(file_version),
    )
