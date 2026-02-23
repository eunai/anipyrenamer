"""Tests for SQLite cache."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from anipyrenamer.cache import (
    get_file_info,
    get_db_path,
    init_db,
    record_renames,
    set_file_info,
)
from anipyrenamer.models import FileInfo


def test_get_db_path_default() -> None:
    p = get_db_path(None)
    assert "anipyrenamer_cache" in p and p.endswith(".sqlite")


def test_get_db_path_custom() -> None:
    assert get_db_path("/tmp/custom.db") == "/tmp/custom.db"


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


def test_record_renames(tmp_path: Path) -> None:
    db = str(tmp_path / "test.sqlite")
    init_db(db)
    record_renames(db, [("/old/path.mkv", "/new/path.mkv")])
    # Just ensure no exception; could query rename_history
    set_file_info(db, FileInfo(1, 2, 3, 4, 100, "e" * 32, "high", "TV"))
    got = get_file_info(db, 100, "e" * 32)
    assert got is not None
