"""Tests for SQLite cache."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from anipyrenamer.cache import (
    CACHE_STALE_SECONDS,
    CacheLookup,
    CacheOutcome,
    get_usable_file_info,
    clear_file_anidb_cache,
    clear_file_anidb_entries,
    get_file_info,
    get_db_path,
    init_db,
    set_file_info,
)
from anipyrenamer.models import FileInfo


def test_get_db_path_default() -> None:
    p = get_db_path(None)
    assert p.endswith("anipyrenamer_cache.sqlite")
    assert ".cache" in p
    assert Path(p).is_absolute()


def test_get_db_path_default_under_project_root_when_cwd_is_repo() -> None:
    """When cwd is the project root (e.g. running pytest from repo), default path is repo/.cache/."""
    from anipyrenamer.cache import _find_project_root

    root = _find_project_root()
    if root is None:
        return
    p = get_db_path(None)
    assert Path(p).resolve().parent == (root / ".cache").resolve()
    assert Path(p).name == "anipyrenamer_cache.sqlite"


def test_get_db_path_custom() -> None:
    assert get_db_path("/tmp/custom.db") == "/tmp/custom.db"


def test_init_db_rejects_invalid_alter_column_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-08: dynamic ALTER TABLE column names must match ^[a-z_]+$."""
    db = str(tmp_path / "legacy.sqlite")
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE file_anidb (
                ed2k TEXT NOT NULL,
                size INTEGER NOT NULL,
                PRIMARY KEY (ed2k, size)
            )
            """
        )
        conn.commit()
    monkeypatch.setattr(
        "anipyrenamer.cache.FILE_ANIDB_EXTRA_COLUMNS",
        ['"; DROP TABLE file_anidb; --'],
    )
    with pytest.raises(ValueError, match="Invalid column name"):
        init_db(db)


def test_init_db_accepts_valid_extra_column_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-08 / P3-D: a well-formed extra column name passes the ^[a-z_]+$ guard."""
    db = str(tmp_path / "valid_col.sqlite")
    monkeypatch.setattr(
        "anipyrenamer.cache.FILE_ANIDB_EXTRA_COLUMNS",
        ["test_col"],
    )
    init_db(db)
    with sqlite3.connect(db) as conn:
        cur = conn.execute("PRAGMA table_info(file_anidb)")
        cols = {row[1] for row in cur.fetchall()}
    assert "test_col" in cols


def test_init_db_creates_parent_directory(tmp_path: Path) -> None:
    """init_db creates parent dir so .cache/ is created when using default path."""
    db = tmp_path / "subdir" / "cache.sqlite"
    assert not db.parent.exists()
    init_db(str(db))
    assert db.parent.exists()
    assert db.exists()


def test_init_db_calls_ensure_owner_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "subdir" / "cache.sqlite"
    calls: list[str] = []

    def _fake_ensure(path: str) -> None:
        calls.append(path)

    monkeypatch.setattr("anipyrenamer.cache.ensure_owner_only", _fake_ensure)
    init_db(str(db))
    assert calls == [str(db)]


def test_init_db_and_file_info(tmp_path: Path) -> None:
    db = str(tmp_path / "test.sqlite")
    init_db(db)
    info = FileInfo(
        fid=1,
        aid=2,
        eid=3,
        gid=4,
        size=100,
        ed2k="A" * 32,
        quality="high",
        source="TV",
    )
    set_file_info(db, info)
    got = get_file_info(db, 100, "A" * 32)
    assert got is not None
    assert got.fid == 1 and got.aid == 2 and got.ed2k == "A" * 32


def test_get_file_info_missing_returns_none(tmp_path: Path) -> None:
    db = str(tmp_path / "test.sqlite")
    init_db(db)
    assert get_file_info(db, 999, "x" * 32) is None


def test_get_file_info_stale_row_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cache rows older than the TTL are treated as misses."""
    db = str(tmp_path / "test.sqlite")
    init_db(db)
    info = FileInfo(1, 2, 3, 4, 100, "a" * 32, "high", "TV")
    set_file_info(db, info)

    now = 1_000_000_000.0
    stale_cached_at = now - CACHE_STALE_SECONDS - 1
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE file_anidb SET cached_at = ? WHERE size = ? AND ed2k = ?",
            (stale_cached_at, 100, "a" * 32),
        )
        conn.commit()

    monkeypatch.setattr("anipyrenamer.cache.time.time", lambda: now)
    assert get_file_info(db, 100, "a" * 32) is None


def test_clear_file_anidb_entries(tmp_path: Path) -> None:
    db = str(tmp_path / "test.sqlite")
    init_db(db)
    set_file_info(db, FileInfo(1, 2, 3, 4, 100, "e" * 32, "high", "TV"))
    set_file_info(db, FileInfo(5, 6, 7, 8, 200, "f" * 32, "low", "DVD"))
    assert get_file_info(db, 100, "e" * 32) is not None
    assert get_file_info(db, 200, "f" * 32) is not None
    n = clear_file_anidb_entries(db, [(100, "e" * 32)])
    assert n == 1
    assert get_file_info(db, 100, "e" * 32) is None
    assert get_file_info(db, 200, "f" * 32) is not None
    assert clear_file_anidb_entries(db, []) == 0


def test_clear_file_anidb_cache(tmp_path: Path) -> None:
    db = str(tmp_path / "test.sqlite")
    init_db(db)
    set_file_info(db, FileInfo(1, 2, 3, 4, 100, "e" * 32, "high", "TV"))
    clear_file_anidb_cache(db)
    assert get_file_info(db, 100, "e" * 32) is None


# ---------------------------------------------------------------------------
# Usable-cache seam (#28, slice C): run-level usability policy layered on the
# get_file_info contract.
# ---------------------------------------------------------------------------


def _seed_entry(db: str, *, title: str = "Show", fid: int = 1) -> None:
    set_file_info(
        db,
        FileInfo(
            fid=fid,
            aid=2,
            eid=3,
            gid=4,
            size=100,
            ed2k="a" * 32,
            quality="high",
            source="TV",
            anime_title=title,
        ),
    )


def _usable(db: str, *, refresh: bool, allow_refetch: bool) -> "CacheLookup":
    return get_usable_file_info(db, 100, "a" * 32, refresh=refresh, allow_refetch=allow_refetch)


def test_usable_fresh_entry_is_usable(tmp_path: Path) -> None:
    """S1: a fresh entry with no overrides is USABLE and carries the FileInfo."""
    db = str(tmp_path / "t.sqlite")
    init_db(db)
    _seed_entry(db)
    lookup = _usable(db, refresh=False, allow_refetch=True)
    assert lookup.outcome is CacheOutcome.USABLE
    assert lookup.info is not None and lookup.info.fid == 1


def test_usable_absent_entry_is_miss(tmp_path: Path) -> None:
    """S2: no entry for the key is a MISS with no FileInfo."""
    db = str(tmp_path / "t.sqlite")
    init_db(db)
    lookup = _usable(db, refresh=False, allow_refetch=True)
    assert lookup.outcome is CacheOutcome.MISS
    assert lookup.info is None


def test_usable_stale_entry_is_miss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """S3: an entry past the staleness window is a MISS (current get_file_info contract)."""
    db = str(tmp_path / "t.sqlite")
    init_db(db)
    _seed_entry(db)
    now = 1_000_000_000.0
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE file_anidb SET cached_at = ? WHERE size = ? AND ed2k = ?",
            (now - CACHE_STALE_SECONDS - 1, 100, "a" * 32),
        )
        conn.commit()
    monkeypatch.setattr("anipyrenamer.cache.time.time", lambda: now)
    lookup = _usable(db, refresh=False, allow_refetch=True)
    assert lookup.outcome is CacheOutcome.MISS
    assert lookup.info is None


def test_usable_refresh_forces_miss_only_when_refetch_allowed(tmp_path: Path) -> None:
    """S4: refresh discards the entry when refetch is allowed; without a refetch path it does not."""
    db = str(tmp_path / "t.sqlite")
    init_db(db)
    _seed_entry(db)
    forced = _usable(db, refresh=True, allow_refetch=True)
    assert forced.outcome is CacheOutcome.MISS
    assert forced.info is None
    kept = _usable(db, refresh=True, allow_refetch=False)
    assert kept.outcome is CacheOutcome.USABLE
    assert kept.info is not None


def test_usable_hash_looking_title_is_repair_when_refetch_allowed(tmp_path: Path) -> None:
    """S5: a hash-looking cached title with a refetch path is REPAIR with no FileInfo."""
    db = str(tmp_path / "t.sqlite")
    init_db(db)
    _seed_entry(db, title="e" * 32)
    lookup = _usable(db, refresh=False, allow_refetch=True)
    assert lookup.outcome is CacheOutcome.REPAIR
    assert lookup.info is None


def test_usable_hash_looking_title_stays_usable_without_refetch(tmp_path: Path) -> None:
    """S6: with no refetch path a hash-looking cached title is NOT discarded (slice-1 preservation)."""
    db = str(tmp_path / "t.sqlite")
    init_db(db)
    _seed_entry(db, title="e" * 32)
    lookup = _usable(db, refresh=False, allow_refetch=False)
    assert lookup.outcome is CacheOutcome.USABLE
    assert lookup.info is not None and lookup.info.anime_title == "e" * 32


def test_usable_refresh_takes_precedence_over_repair(tmp_path: Path) -> None:
    """S7: refresh + hash-looking title yields MISS, not REPAIR (refresh-discard checked first)."""
    db = str(tmp_path / "t.sqlite")
    init_db(db)
    _seed_entry(db, title="e" * 32)
    lookup = _usable(db, refresh=True, allow_refetch=True)
    assert lookup.outcome is CacheOutcome.MISS
    assert lookup.info is None


def test_cache_lookup_is_frozen(tmp_path: Path) -> None:
    """CacheLookup is immutable; callers cannot mutate the decision."""
    db = str(tmp_path / "t.sqlite")
    init_db(db)
    _seed_entry(db)
    lookup = _usable(db, refresh=False, allow_refetch=True)
    with pytest.raises(AttributeError):
        lookup.outcome = CacheOutcome.MISS  # type: ignore[misc]
