"""Tests for SQLite cache."""

from __future__ import annotations

from pathlib import Path


from anipyrenamer.cache import (
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


def test_init_db_creates_parent_directory(tmp_path: Path) -> None:
    """init_db creates parent dir so .cache/ is created when using default path."""
    db = tmp_path / "subdir" / "cache.sqlite"
    assert not db.parent.exists()
    init_db(str(db))
    assert db.parent.exists()
    assert db.exists()


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
