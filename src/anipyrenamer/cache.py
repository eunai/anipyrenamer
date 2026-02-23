"""SQLite cache: file_anidb (lookup by size+ed2k), rename_history (for apply/undo)."""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

from anipyrenamer.models import FileInfo

CACHE_STALE_DAYS = 30
CACHE_STALE_SECONDS = CACHE_STALE_DAYS * 24 * 60 * 60


def get_db_path(db_path: str | None) -> str:
    """Default DB in user cache or current dir; else use provided path."""
    if db_path:
        return db_path
    return str(Path.cwd() / "anipyrenamer_cache.sqlite")


def init_db(db_path: str) -> None:
    """Create tables if they do not exist."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS file_anidb (
                ed2k TEXT NOT NULL,
                size INTEGER NOT NULL,
                fid INTEGER NOT NULL,
                aid INTEGER NOT NULL,
                eid INTEGER NOT NULL,
                gid INTEGER NOT NULL,
                quality TEXT NOT NULL,
                source TEXT NOT NULL,
                cached_at REAL NOT NULL,
                anime_title TEXT,
                episode_number TEXT,
                episode_title TEXT,
                group_name TEXT,
                file_version TEXT,
                PRIMARY KEY (ed2k, size)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rename_history (
                old_path TEXT NOT NULL,
                new_path TEXT NOT NULL,
                applied_at REAL NOT NULL,
                batch_id TEXT NOT NULL
            )
            """
        )
        conn.commit()


def get_file_info(db_path: str, size: int, ed2k: str) -> FileInfo | None:
    """Return cached FileInfo if present and not stale (>30 days)."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM file_anidb WHERE ed2k = ? AND size = ?", (ed2k, size)
        ).fetchone()
        if not row:
            return None
        cached_at = row["cached_at"]
        if time.time() - cached_at > CACHE_STALE_SECONDS:
            return None
        return _row_to_file_info(row)


def set_file_info(db_path: str, info: FileInfo) -> None:
    """Upsert file_anidb row."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO file_anidb (
                ed2k, size, fid, aid, eid, gid, quality, source, cached_at,
                anime_title, episode_number, episode_title, group_name, file_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                info.ed2k,
                info.size,
                info.fid,
                info.aid,
                info.eid,
                info.gid,
                info.quality,
                info.source,
                time.time(),
                info.anime_title,
                info.episode_number,
                info.episode_title,
                info.group_name,
                info.file_version,
            ),
        )
        conn.commit()


def record_renames(db_path: str, items: list[tuple[str, str]], batch_id: str | None = None) -> None:
    """Append rename_history rows. batch_id generated if not provided."""
    bid = batch_id or str(uuid.uuid4())
    now = time.time()
    with sqlite3.connect(db_path) as conn:
        for old_path, new_path in items:
            conn.execute(
                "INSERT INTO rename_history (old_path, new_path, applied_at, batch_id) VALUES (?, ?, ?, ?)",
                (old_path, new_path, now, bid),
            )
        conn.commit()


def _row_to_file_info(row: sqlite3.Row) -> FileInfo:
    return FileInfo(
        fid=row["fid"],
        aid=row["aid"],
        eid=row["eid"],
        gid=row["gid"],
        size=row["size"],
        ed2k=row["ed2k"],
        quality=row["quality"] or "",
        source=row["source"] or "",
        group_name=row["group_name"] or "",
        anime_title=row["anime_title"] or "",
        episode_number=row["episode_number"] or "",
        episode_title=row["episode_title"] or "",
        file_version=row["file_version"] or "",
    )
