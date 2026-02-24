"""AniDB UDP client: AUTH, FILE, throttle (burst 5, then 1 per 2.5s)."""

from __future__ import annotations

import os
import re
import socket
import time
from dataclasses import dataclass

from anipyrenamer.models import FileInfo

ANIDB_HOST = "api.anidb.net"
ANIDB_PORT = 9000
FILE_FMASK = "79FAFFE900"
FILE_AMASK = "F2FCF0C0"
# Throttle: first 5 packets, then 1 packet per 2.5 s
BURST_SIZE = 5
PACKET_INTERVAL = 2.5


@dataclass
class AniDBConfig:
    """Credentials and client info from env or args."""

    username: str
    password: str
    client: str
    clientver: str
    local_port: int

    @classmethod
    def from_env(cls) -> AniDBConfig:
        """Load from environment (e.g. .env via python-dotenv)."""
        return cls(
            username=os.environ.get("ANIDB_USERNAME", ""),
            password=os.environ.get("ANIDB_PASSWORD", ""),
            client=os.environ.get("ANIDB_UDP_CLIENT", "anipyrenamer"),
            clientver=os.environ.get("ANIDB_UDP_CLIENTVER", "1"),
            local_port=int(os.environ.get("ANIDB_LOCAL_PORT", "0") or "0"),
        )


def _looks_like_hash(s: str) -> bool:
    """True if string looks like a hex hash (CRC32, MD5, SHA1, ED2K, etc.) - do not use as title/group."""
    if len(s) < 8:
        return False
    allowed = set("0123456789abcdefABCDEF-")
    if not all(c in allowed for c in s):
        return False
    # CRC32 = 8 hex chars; MD5=32, SHA1=40, ED2K=32
    if len(s) == 8 and sum(c in "abcdefABCDEF" for c in s) >= 2:
        return True
    if len(s) >= 16 and sum(c in "abcdefABCDEF" for c in s) >= 2:
        return True
    return False


class AniDBClient:
    """UDP client with throttle: burst 5, then 1 per 2.5s."""

    def __init__(self, config: AniDBConfig, *, debug: bool = False) -> None:
        self._config = config
        self._debug = debug
        self._sock: socket.socket | None = None
        self._session: str | None = None
        self._packets_sent = 0
        self._burst_start: float = 0.0

    def _ensure_socket(self) -> socket.socket:
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if self._config.local_port > 0:
                self._sock.bind(("", self._config.local_port))
            self._sock.settimeout(15.0)
        return self._sock

    def _throttle(self) -> None:
        now = time.monotonic()
        if self._packets_sent == 0:
            self._burst_start = now
        self._packets_sent += 1
        if self._packets_sent <= BURST_SIZE:
            return
        # After burst: wait so we don't exceed 1 per 2.5s
        elapsed = now - self._burst_start
        required = (self._packets_sent - BURST_SIZE) * PACKET_INTERVAL
        if required > elapsed:
            time.sleep(required - elapsed)

    def _send_recv(self, msg: str) -> str:
        self._throttle()
        sock = self._ensure_socket()
        if self._debug:
            log_msg = re.sub(r"pass=\w+", "pass=***", msg)
            print(f"[anidb] >>> {log_msg}")
        sock.sendto(msg.encode("utf-8"), (ANIDB_HOST, ANIDB_PORT))
        data, _ = sock.recvfrom(4096)
        reply = data.decode("utf-8", errors="replace").strip()
        if self._debug:
            preview = reply[:500] + "..." if len(reply) > 500 else reply
            print(f"[anidb] <<< {preview}")
        return reply

    def login(self) -> tuple[bool, str]:
        """AUTH; store session key. Returns (True, '') if 200/201, else (False, reply)."""
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
            return (True, "")
        return (False, reply)

    def logout(self) -> None:
        """LOGOUT and close socket. Uses a short timeout so the server reliably sees LOGOUT
        (avoids 'useless connect' when LOGOUT would otherwise time out)."""
        if self._session and self._sock:
            prev_timeout = self._sock.gettimeout()
            try:
                self._sock.settimeout(5.0)
                for _ in range(2):
                    try:
                        self._send_recv(f"LOGOUT s={self._session}")
                        break
                    except Exception:
                        self._sock.settimeout(5.0)
            finally:
                try:
                    self._sock.settimeout(prev_timeout)
                except Exception:
                    pass
            self._session = None
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

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
            info.ep_count = (parts[2] or "").strip()
            info.year_begin = (parts[10] or "").strip()
            info.year_end = info.year_begin  # single year in default format
            info.anime_type = (parts[11] or "").strip()
            info.title_romaji = (parts[12] or "").strip()
            info.title_kanji = (parts[13] or "").strip()
            info.title_english = (parts[14] or "").strip()
            info.title_other = (parts[15] or "").strip()
            info.title_synonym = (parts[17] or "").strip()
            info.categories = (parts[18] or "").strip()
        if len(parts) >= 15:
            info.title_romaji = info.title_romaji or (parts[12] or "").strip()
            info.title_english = info.title_english or (parts[14] or "").strip()
        # Prefer ANIME titles over FILE line heuristic (which can misparse quality/codec as title)
        anime_from_anime = info.title_english or info.title_romaji or info.title_kanji or ""
        if anime_from_anime:
            info.anime_title = anime_from_anime
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
            epno = (parts[5] or "").strip()
            if epno:
                info.episode_number = epno
            info.eptitle_english = (parts[6] or "").strip()
            info.eptitle_romaji = (parts[7] or "").strip()
            info.eptitle_kanji = (parts[8] or "").strip()
        if len(parts) >= 7:
            eptitle = (parts[6] or "").strip()
            if eptitle:
                info.episode_title = eptitle
        if not info.episode_title and info.eptitle_english:
            info.episode_title = info.eptitle_english

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
        long_name = (parts[5] or "").strip() if len(parts) > 5 else ""
        short_name = (parts[6] or "").strip() if len(parts) > 6 else ""
        return (short_name, long_name)

    def __enter__(self) -> AniDBClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.logout()


def _parse_file_response(data_line: str, size: int, ed2k: str) -> FileInfo:
    """Parse one line of 220 FILE response. We know size and ed2k from request."""
    parts = [p.strip() for p in data_line.split("|")]
    # fid, aid, eid, gid are first four per spec
    fid = int(parts[0]) if len(parts) > 0 else 0
    aid = int(parts[1]) if len(parts) > 1 else 0
    eid = int(parts[2]) if len(parts) > 2 else 0
    gid = int(parts[3]) if len(parts) > 3 else 0
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
            quality = p
        elif p in ("TV", "DTV", "DVD", "VHS", "HDTV", "LD", "WEB", "Blu-ray", "Blu-Ray"):
            source = p
        elif _looks_like_hash(p):
            continue  # do not use hash as title/group
        elif i >= 4 and len(p) > 2 and not p.isdigit() and "|" not in p:
            if not anime_title and p:
                anime_title = p
            elif anime_title and not episode_number and re.match(r"^\d+", p):
                episode_number = p
            elif episode_number and not episode_title and p and p != episode_number:
                episode_title = p
            elif not group_name and p and p != episode_title:
                group_name = p
    # Prefer extracting by position if we have enough fields; otherwise use defaults
    if len(parts) > 10:
        quality = quality or (parts[10] if parts[10] else "")
        source = source or (parts[11] if len(parts) > 11 else "")
    return FileInfo(
        fid=fid,
        aid=aid,
        eid=eid,
        gid=gid,
        size=size,
        ed2k=ed2k,
        quality=quality,
        source=source,
        group_name=group_name,
        group_short_name="",
        anime_title=anime_title,
        episode_number=episode_number,
        episode_title=episode_title,
        file_version=file_version,
    )
