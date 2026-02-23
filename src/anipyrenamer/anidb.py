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


class AniDBClient:
    """UDP client with throttle: burst 5, then 1 per 2.5s."""

    def __init__(self, config: AniDBConfig) -> None:
        self._config = config
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
        sock.sendto(msg.encode("utf-8"), (ANIDB_HOST, ANIDB_PORT))
        data, _ = sock.recvfrom(4096)
        return data.decode("utf-8", errors="replace").strip()

    def login(self) -> bool:
        """AUTH; store session key. Returns True if 200/201."""
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
            return True
        return False

    def logout(self) -> None:
        """LOGOUT and close socket."""
        if self._session:
            try:
                self._send_recv(f"LOGOUT s={self._session}")
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
            info.anime_title = self._anime_title(info.aid) or info.anime_title
        if info.eid:
            epno, eptitle = self._episode_info(info.eid)
            if epno:
                info.episode_number = epno
            if eptitle:
                info.episode_title = eptitle
        if info.gid:
            info.group_name = self._group_name(info.gid) or info.group_name
        return info

    def _anime_title(self, aid: int) -> str:
        """ANIME aid=; return english or romaji name."""
        if not self._session:
            return ""
        reply = self._send_recv(f"ANIME aid={aid}&s={self._session}")
        if "230 ANIME" not in reply:
            return ""
        lines = reply.split("\n")
        if len(lines) < 2:
            return ""
        parts = lines[1].strip().split("|")
        # Default format: aid|eps|...|year|type|romaji|kanji|english|...
        if len(parts) >= 15:
            return (parts[14] or parts[12] or "").strip()  # english then romaji
        if len(parts) >= 13:
            return (parts[12] or "").strip()
        return ""

    def _episode_info(self, eid: int) -> tuple[str, str]:
        """EPISODE eid=; return (epno, episode_title)."""
        if not self._session:
            return ("", "")
        reply = self._send_recv(f"EPISODE eid={eid}&s={self._session}")
        if "240 EPISODE" not in reply:
            return ("", "")
        lines = reply.split("\n")
        if len(lines) < 2:
            return ("", "")
        parts = lines[1].strip().split("|")
        # eid|aid|length|rating|votes|epno|eng|romaji|kanji|aired|type
        if len(parts) >= 8:
            return (parts[5] or "", parts[6] or "")
        if len(parts) >= 6:
            return (parts[5] or "", "")
        return ("", "")

    def _group_name(self, gid: int) -> str:
        """GROUP gid=; return group name."""
        if not self._session or gid <= 0:
            return ""
        reply = self._send_recv(f"GROUP gid={gid}&s={self._session}")
        if "250 GROUP" not in reply:
            return ""
        lines = reply.split("\n")
        if len(lines) < 2:
            return ""
        parts = lines[1].strip().split("|")
        if len(parts) >= 6:
            return (parts[5] or "").strip()
        return ""

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
    # Scan for common string fields (quality, source, names appear in response)
    for i, p in enumerate(parts):
        if p in ("high", "medium", "low", "corrupt", "very high", "backup", "unknown"):
            quality = p
        elif p in ("TV", "DTV", "DVD", "VHS", "HDTV", "LD", "WEB", "Blu-ray", "Blu-Ray"):
            source = p
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
        anime_title=anime_title,
        episode_number=episode_number,
        episode_title=episode_title,
        file_version=file_version,
    )
