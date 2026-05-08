"""SQLite cache: file_anidb (lookup by size+ed2k)."""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path

from anipyrenamer.models import FileInfo
from anipyrenamer.permissions import ensure_owner_only

CACHE_FILENAME = "anipyrenamer_cache.sqlite"

CACHE_STALE_DAYS = 30
CACHE_STALE_SECONDS = CACHE_STALE_DAYS * 24 * 60 * 60

# Extra columns for AniAdd-style variables (migration adds if missing)
FILE_ANIDB_EXTRA_COLUMNS = [
    "title_romaji",
    "title_english",
    "title_kanji",
    "title_synonym",
    "title_other",
    "eptitle_romaji",
    "eptitle_english",
    "eptitle_kanji",
    "ep_count",
    "ep_highest",
    "year_begin",
    "year_end",
    "categories",
    "anime_type",
    "deprecated",
    "censored",
    "anidb_filename",
    "crc",
    "video_resolution",
    "audio_codec",
    "video_codec",
    "audio_langs",
    "subtitle_langs",
    "duration",
    "watched",
]


def _get_well_known_cache_dir() -> Path | None:
    """Base dir for cache when not in a project (Windows: %APPDATA%/anipyrenamer, Unix: ~/.config/anipyrenamer)."""
    if os.name == "nt":
        apd = os.environ.get("APPDATA")
        if not apd:
            return None
        return Path(apd) / "anipyrenamer"
    return Path.home() / ".config" / "anipyrenamer"


def _find_project_root() -> Path | None:
    """Walk up from cwd; return directory containing pyproject.toml if found."""
    candidate = Path.cwd().resolve()
    for _ in range(32):
        if (candidate / "pyproject.toml").is_file():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return None


def _get_default_db_path() -> str:
    """Default cache path: project .cache/ when in repo, else well-known config .cache/ (never cwd)."""
    project_root = _find_project_root()
    if project_root is not None:
        return str(project_root / ".cache" / CACHE_FILENAME)
    well_known = _get_well_known_cache_dir()
    if well_known is not None:
        return str(well_known / ".cache" / CACHE_FILENAME)
    return str(Path.home() / ".cache" / "anipyrenamer" / CACHE_FILENAME)


def get_db_path(db_path: str | None) -> str:
    """Default DB in project or well-known .cache/; else use provided path."""
    if db_path:
        return db_path
    return _get_default_db_path()


def init_db(db_path: str) -> None:
    """Create tables if they do not exist; add any missing columns to file_anidb."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
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
                group_short_name TEXT,
                file_version TEXT,
                title_romaji TEXT,
                title_english TEXT,
                title_kanji TEXT,
                title_synonym TEXT,
                title_other TEXT,
                eptitle_romaji TEXT,
                eptitle_english TEXT,
                eptitle_kanji TEXT,
                ep_count TEXT,
                ep_highest TEXT,
                year_begin TEXT,
                year_end TEXT,
                categories TEXT,
                anime_type TEXT,
                deprecated TEXT,
                censored TEXT,
                anidb_filename TEXT,
                crc TEXT,
                video_resolution TEXT,
                audio_codec TEXT,
                video_codec TEXT,
                audio_langs TEXT,
                subtitle_langs TEXT,
                duration TEXT,
                watched TEXT,
                PRIMARY KEY (ed2k, size)
            )
            """
        )
        cur = conn.execute("PRAGMA table_info(file_anidb)")
        existing = {row[1] for row in cur.fetchall()}
        for col in FILE_ANIDB_EXTRA_COLUMNS:
            if col not in existing:
                if not re.fullmatch(r"[a-z_]+", col):
                    raise ValueError(f"Invalid column name: {col}")
                conn.execute(f"ALTER TABLE file_anidb ADD COLUMN {col} TEXT")
        conn.commit()
    ensure_owner_only(db_path)


def clear_file_anidb_cache(db_path: str) -> None:
    """Delete all rows from file_anidb so lookups will refetch from AniDB."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM file_anidb")
        conn.commit()


def clear_file_anidb_entries(db_path: str, entries: list[tuple[int, str]]) -> int:
    """
    Delete file_anidb rows for the given (size, ed2k) pairs.
    Returns the number of rows deleted.
    """
    if not entries:
        return 0
    with sqlite3.connect(db_path) as conn:
        total = 0
        for size, ed2k in entries:
            cur = conn.execute("DELETE FROM file_anidb WHERE size = ? AND ed2k = ?", (size, ed2k))
            total += cur.rowcount
        conn.commit()
        return total


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


def _optional(row: sqlite3.Row, key: str) -> str:
    return (row[key] or "") if key in row.keys() else ""


def set_file_info(db_path: str, info: FileInfo) -> None:
    """Upsert file_anidb row with all fields (cached for template tokens)."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO file_anidb (
                ed2k, size, fid, aid, eid, gid, quality, source, cached_at,
                anime_title, episode_number, episode_title, group_name, group_short_name, file_version,
                title_romaji, title_english, title_kanji, title_synonym, title_other,
                eptitle_romaji, eptitle_english, eptitle_kanji,
                ep_count, ep_highest, year_begin, year_end, categories, anime_type,
                deprecated, censored, anidb_filename, crc, video_resolution,
                audio_codec, video_codec, audio_langs, subtitle_langs, duration, watched
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                info.group_short_name,
                info.file_version,
                info.title_romaji,
                info.title_english,
                info.title_kanji,
                info.title_synonym,
                info.title_other,
                info.eptitle_romaji,
                info.eptitle_english,
                info.eptitle_kanji,
                info.ep_count,
                info.ep_highest,
                info.year_begin,
                info.year_end,
                info.categories,
                info.anime_type,
                info.deprecated,
                info.censored,
                info.anidb_filename,
                info.crc,
                info.video_resolution,
                info.audio_codec,
                info.video_codec,
                info.audio_langs,
                info.subtitle_langs,
                info.duration,
                info.watched,
            ),
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
        group_short_name=_optional(row, "group_short_name"),
        anime_title=row["anime_title"] or "",
        episode_number=row["episode_number"] or "",
        episode_title=row["episode_title"] or "",
        file_version=row["file_version"] or "",
        title_romaji=_optional(row, "title_romaji"),
        title_english=_optional(row, "title_english"),
        title_kanji=_optional(row, "title_kanji"),
        title_synonym=_optional(row, "title_synonym"),
        title_other=_optional(row, "title_other"),
        eptitle_romaji=_optional(row, "eptitle_romaji"),
        eptitle_english=_optional(row, "eptitle_english"),
        eptitle_kanji=_optional(row, "eptitle_kanji"),
        ep_count=_optional(row, "ep_count"),
        ep_highest=_optional(row, "ep_highest"),
        year_begin=_optional(row, "year_begin"),
        year_end=_optional(row, "year_end"),
        categories=_optional(row, "categories"),
        anime_type=_optional(row, "anime_type"),
        deprecated=_optional(row, "deprecated"),
        censored=_optional(row, "censored"),
        anidb_filename=_optional(row, "anidb_filename"),
        crc=_optional(row, "crc"),
        video_resolution=_optional(row, "video_resolution"),
        audio_codec=_optional(row, "audio_codec"),
        video_codec=_optional(row, "video_codec"),
        audio_langs=_optional(row, "audio_langs"),
        subtitle_langs=_optional(row, "subtitle_langs"),
        duration=_optional(row, "duration"),
        watched=_optional(row, "watched"),
    )
