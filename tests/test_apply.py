"""Tests for preview and apply."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from anipyrenamer.apply import apply_plan, preview_plan
from anipyrenamer.models import RenameItem


def test_preview_plan_no_crash(capsys: pytest.CaptureFixture[str]) -> None:
    items = [RenameItem("/a/x.mkv", "/b/y.mkv")]
    preview_plan(items)
    # Rich prints to console; just ensure no exception
    assert True


def test_apply_plan_dry_run_does_nothing(tmp_path: Path) -> None:
    src = tmp_path / "orig.mkv"
    src.write_bytes(b"data")
    items = [RenameItem(str(src), str(tmp_path / "new.mkv"))]
    apply_plan(items, str(tmp_path / "db.sqlite"), dry_run=True, record=False)
    assert src.exists()
    assert not (tmp_path / "new.mkv").exists()


def test_apply_plan_moves_file(tmp_path: Path) -> None:
    src = tmp_path / "orig.mkv"
    src.write_bytes(b"data")
    db = tmp_path / "cache.sqlite"
    from anipyrenamer.cache import init_db
    init_db(str(db))
    items = [RenameItem(str(src), str(tmp_path / "new.mkv"))]
    apply_plan(items, str(db), dry_run=False, record=True)
    assert not src.exists()
    assert (tmp_path / "new.mkv").exists()
    assert (tmp_path / "new.mkv").read_bytes() == b"data"


def test_apply_plan_files_before_folders(tmp_path: Path) -> None:
    """Apply order must be files first, then folders, so the file ends up in the renamed folder."""
    from anipyrenamer.cache import init_db

    old_dir = tmp_path / "OldDir"
    old_dir.mkdir()
    file_path = old_dir / "ep.mkv"
    file_path.write_bytes(b"video")
    new_dir_name = "NewDir"
    new_file_name = "renamed.mkv"
    db = tmp_path / "cache.sqlite"
    init_db(str(db))
    # Item order: folder rename first, then file rename. apply_plan must still do file then folder.
    items = [
        RenameItem(str(old_dir), str(tmp_path / new_dir_name)),
        RenameItem(str(file_path), str(old_dir / new_file_name)),
    ]
    apply_plan(items, str(db), dry_run=False, record=False)
    # After apply: file moved to OldDir/renamed.mkv, then OldDir renamed to NewDir
    assert not file_path.exists()
    assert not old_dir.exists()
    assert (tmp_path / new_dir_name).is_dir()
    assert (tmp_path / new_dir_name / new_file_name).exists()
    assert (tmp_path / new_dir_name / new_file_name).read_bytes() == b"video"
