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
