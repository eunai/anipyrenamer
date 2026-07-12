"""SQLite cache: file_anidb (lookup by size+ed2k)."""

from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from anipyrenamer.models import FileInfo, looks_like_hash
from anipyrenamer.permissions import ensure_owner_only

CACHE_FILENAME = "anipyrenamer_cache.sqlite"

# SQLite primary result code (sqlite3.Error.sqlite_errorcode), never message text.
# SQLITE_BUSY is inconclusive lock contention (warn); every other SQLite/OS
# error (SQLITE_READONLY, malformed, SQLITE_IOERR, disk-full, ...) is a proven
# inability to complete the probe (fail) per the Check-4 outcome table.
_SQLITE_BUSY = 5
_PROBE_TIMEOUT = 2.0

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


class CacheProbeOutcome(Enum):
    """Severity of the bounded, non-persistent Check-4 cache operational probe."""

    OPERATIONAL = "operational"
    INCONCLUSIVE = "inconclusive"
    UNUSABLE = "unusable"


@dataclass(frozen=True)
class CacheProbeResult:
    """A sanitized Check-4 outcome: names/paths only, never raw exception text."""

    outcome: CacheProbeOutcome
    detail: str


def _sqlite_errorcode(error: sqlite3.Error) -> int | None:
    return getattr(error, "sqlite_errorcode", None)


def _classify_operational_error(error: sqlite3.Error | OSError) -> CacheProbeOutcome:
    """Classify by SQLite result code / OS errno-winerror, never message text."""
    if isinstance(error, sqlite3.Error):
        code = _sqlite_errorcode(error)
        if code == _SQLITE_BUSY:
            return CacheProbeOutcome.INCONCLUSIVE
        return CacheProbeOutcome.UNUSABLE
    return CacheProbeOutcome.UNUSABLE


def _cleanup_step_name(path: Path) -> str:
    """Categorize a cleanup artifact for the sanitized failure detail (SPEC.md §8)."""
    if path.name.endswith(("-journal", "-wal", "-shm")):
        return "sidecar file"
    if path.suffix == ".sqlite":
        return "temporary probe database"
    return "throwaway directory"


def _cleanup_paths(paths: list[Path]) -> str | None:
    """Remove probe artifacts in reverse order; return a sanitized detail on residue.

    An already-absent artifact is not a cleanup failure. Any artifact that
    remains, or whose removal cannot be verified, dominates to a `fail`. The
    detail names the residual path and which cleanup step failed (SPEC.md §8).
    """
    residue: Path | None = None
    for path in reversed(paths):
        if not path.exists():
            continue
        try:
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        except OSError:
            residue = path
            break
        if path.exists():
            residue = path
            break
    if residue is None:
        return None
    return f"probe cleanup left a residual {_cleanup_step_name(residue)}: {residue}"


def _sqlite_sidecars(db_path: Path) -> list[Path]:
    return [
        db_path,
        Path(str(db_path) + "-journal"),
        Path(str(db_path) + "-wal"),
        Path(str(db_path) + "-shm"),
    ]


def _deep_minimal_probe(parent: Path) -> tuple[CacheProbeOutcome, str, list[Path]]:
    """Let SQLite create a uniquely-named temp DB under ``parent``; write, commit, no pre-create.

    Returns the outcome, a sanitized detail, and the artifact paths to clean up.
    """
    probe_path = parent / f".anipyrenamer-doctor-{os.getpid()}-{time.time_ns()}.sqlite"
    artifacts = _sqlite_sidecars(probe_path)
    try:
        connection = sqlite3.connect(probe_path, timeout=_PROBE_TIMEOUT)
        try:
            connection.execute("CREATE TABLE doctor_probe (ok INTEGER NOT NULL)")
            connection.commit()
        finally:
            connection.close()
        return CacheProbeOutcome.OPERATIONAL, f"parent is operationally usable: {parent}", artifacts
    except (sqlite3.Error, OSError) as error:
        return (
            _classify_operational_error(error),
            f"parent is not operationally usable: {parent}",
            artifacts,
        )


def _probe_and_cleanup(directory: Path) -> CacheProbeResult:
    """Run the deep-minimal probe in ``directory``; cleanup always runs, even on an escaped error.

    Cleanup residue or an unverifiable cleanup dominates to `UNUSABLE` per the
    Check-4 cleanup-dominates rule (SPEC.md §8 / design note).
    """
    artifacts: list[Path] = []
    try:
        outcome, detail, artifacts = _deep_minimal_probe(directory)
        return CacheProbeResult(outcome, detail)
    finally:
        cleanup_detail = _cleanup_paths(artifacts)
        if cleanup_detail is not None:
            return CacheProbeResult(CacheProbeOutcome.UNUSABLE, cleanup_detail)  # noqa: B012


def _nearest_existing_ancestor(path: Path) -> tuple[Path, int]:
    """Return the nearest existing ancestor of ``path`` and the number of missing levels."""
    missing = 0
    candidate = path
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
        missing += 1
    return candidate, missing


def _throwaway_ancestor_probe(missing_parent: Path) -> CacheProbeResult:
    """Missing parents: probe an equivalent-depth throwaway tree under the nearest ancestor."""
    ancestor, missing_levels = _nearest_existing_ancestor(missing_parent)
    if missing_levels == 0:
        # The direct parent exists after all; fall through to the deep-minimal probe.
        return _probe_and_cleanup(missing_parent)

    root = ancestor / f".anipyrenamer-doctor-{os.getpid()}-{time.time_ns()}"
    tree_dirs: list[Path] = []
    deepest = root
    for _ in range(missing_levels):
        tree_dirs.append(deepest)
        deepest = deepest / "d"
    # `deepest` is one level past the last created dir; the deepest created dir
    # is where the temp DB probe runs (mirrors the missing parent itself).
    deepest_dir = tree_dirs[-1] if tree_dirs else root

    created: list[Path] = []
    outcome = CacheProbeOutcome.UNUSABLE
    db_artifacts: list[Path] = []
    try:
        for directory in tree_dirs:
            try:
                directory.mkdir(parents=False, exist_ok=False)
                created.append(directory)
            except OSError as error:
                outcome = _classify_operational_error(error)
                return CacheProbeResult(
                    outcome,
                    f"nearest existing ancestor could not accept the equivalent tree: {ancestor}",
                )
        outcome, _detail, db_artifacts = _deep_minimal_probe(deepest_dir)
        if outcome == CacheProbeOutcome.OPERATIONAL:
            return CacheProbeResult(
                CacheProbeOutcome.OPERATIONAL,
                "the cache directory is absent, but the nearest existing ancestor accepted "
                f"the equivalent operations; exact-path creation remains unverified: {ancestor}",
            )
        return CacheProbeResult(
            outcome, f"nearest existing ancestor could not accept the equivalent tree: {ancestor}"
        )
    finally:
        cleanup_detail = _cleanup_paths(created + db_artifacts)
        if cleanup_detail is not None:
            return CacheProbeResult(CacheProbeOutcome.UNUSABLE, cleanup_detail)  # noqa: B012


def _existing_db_probe(db_path: Path) -> CacheProbeResult:
    """Guarded real-file probe: read-only schema check, journal guard, then BEGIN IMMEDIATE/ROLLBACK."""
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=_PROBE_TIMEOUT)
        try:
            connection.execute("PRAGMA schema_version")
        finally:
            connection.close()
    except (sqlite3.Error, OSError) as error:
        return CacheProbeResult(
            _classify_operational_error(error), f"cache is not operationally usable: {db_path}"
        )

    journal = Path(str(db_path) + "-journal")
    if journal.exists() and journal.stat().st_size > 0:
        return CacheProbeResult(
            CacheProbeOutcome.INCONCLUSIVE,
            f"cache has a pending rollback journal; write probe skipped: {db_path}",
        )

    try:
        connection = sqlite3.connect(db_path, timeout=_PROBE_TIMEOUT)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("ROLLBACK")
        finally:
            connection.close()
    except (sqlite3.Error, OSError) as error:
        return CacheProbeResult(
            _classify_operational_error(error), f"cache is not operationally usable: {db_path}"
        )
    return CacheProbeResult(
        CacheProbeOutcome.OPERATIONAL, f"write lock acquired and released: {db_path}"
    )


def probe_cache_operational(db_path: str) -> CacheProbeResult:
    """Bounded, non-persistent Check-4 probe: prove the configured cache path was usable *at probe time*.

    Never calls ``init_db()`` against the configured path and never persistently
    mutates the DB, schema, contents, or parent directory. See SPEC.md §8 and the
    doctor preflight design note for the full contract.
    """
    resolved = Path(db_path)
    if resolved.exists():
        return _existing_db_probe(resolved)

    parent = resolved.parent
    if parent.is_dir():
        return _probe_and_cleanup(parent)

    return _throwaway_ancestor_probe(parent)


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
    """Return cached FileInfo if present; entries never expire by age."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM file_anidb WHERE ed2k = ? AND size = ?", (ed2k, size)
        ).fetchone()
        if not row:
            return None
        return _row_to_file_info(row)


class CacheOutcome(Enum):
    """Why a cache lookup did or did not yield a usable entry."""

    USABLE = "usable"
    MISS = "miss"
    REPAIR = "repair"


@dataclass(frozen=True)
class CacheLookup:
    """Result of the run-level usability decision; info is present only for USABLE."""

    info: FileInfo | None
    outcome: CacheOutcome


def get_usable_file_info(
    db_path: str, size: int, ed2k: str, *, refresh: bool, allow_refetch: bool
) -> CacheLookup:
    """
    Run-level usability policy layered on the get_file_info contract.

    refresh discards the cached entry, and a hash-looking cached title is
    discarded for repair, only when a refetch is possible this run
    (allow_refetch); refresh-discard takes precedence over repair.
    """
    if refresh and allow_refetch:
        return CacheLookup(None, CacheOutcome.MISS)
    info = get_file_info(db_path, size, ed2k)
    if info is None:
        return CacheLookup(None, CacheOutcome.MISS)
    if allow_refetch and looks_like_hash(info.anime_title):
        return CacheLookup(None, CacheOutcome.REPAIR)
    return CacheLookup(info, CacheOutcome.USABLE)


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
