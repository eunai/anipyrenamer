"""Tests for preview and apply."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from anipyrenamer.apply import _plan_sort_key, apply_plan, preview_plan
from anipyrenamer.models import RenameItem, RenameKind


def test_preview_plan_no_crash(capsys: pytest.CaptureFixture[str]) -> None:
    items = [RenameItem("/a/x.mkv", "/b/y.mkv")]
    preview_plan(items)
    # Rich prints to console; just ensure no exception
    assert True


def test_preview_plan_with_skip_item_no_crash(capsys: pytest.CaptureFixture[str]) -> None:
    """Preview table includes SKIP items (AniDB lookup failed) without crashing."""
    items = [
        RenameItem("/path/to/video.mkv", "(AniDB lookup failed)", kind=RenameKind.SKIP),
    ]
    preview_plan(items)
    assert True


def test_preview_plan_sorted_by_folder_then_episode() -> None:
    """Preview table order is by destination folder (case-insensitive) then episode number."""
    root = "C:\\anime"
    items = [
        RenameItem("/any/222222.mkv", f"{root}\\Dan Da Dan [Subs]\\Dan Da Dan 01 - First [Subs].mkv"),
        RenameItem("/any/11.mkv", f"{root}\\Blue Lock [SEV]\\Blue Lock 02 - Monster [SEV].mkv"),
        RenameItem("/any/other.mkv", f"{root}\\Blue Lock [SEV]\\Blue Lock 01 - Dream [SEV].mkv"),
        RenameItem("/any/x.mkv", f"{root}\\Nana [EMBER]\\Nana 01 - Prologue [EMBER].mkv"),
    ]
    ordered = sorted(items, key=_plan_sort_key)
    new_paths = [item.new_path for item in ordered]
    # Blue Lock (01 then 02), then Dan Da Dan 01, then Nana 01
    assert "Blue Lock 01" in new_paths[0]
    assert "Blue Lock 02" in new_paths[1]
    assert "Dan Da Dan 01" in new_paths[2]
    assert "Nana 01" in new_paths[3]


def test_apply_plan_dry_run_does_nothing(tmp_path: Path) -> None:
    src = tmp_path / "orig.mkv"
    src.write_bytes(b"data")
    items = [RenameItem(str(src), str(tmp_path / "new.mkv"))]
    apply_plan(items, str(tmp_path / "db.sqlite"), dry_run=True)
    assert src.exists()
    assert not (tmp_path / "new.mkv").exists()


def test_apply_plan_moves_file(tmp_path: Path) -> None:
    src = tmp_path / "orig.mkv"
    src.write_bytes(b"data")
    db = tmp_path / "cache.sqlite"
    from anipyrenamer.cache import init_db
    init_db(str(db))
    items = [RenameItem(str(src), str(tmp_path / "new.mkv"))]
    apply_plan(items, str(db), dry_run=False)
    assert not src.exists()
    assert (tmp_path / "new.mkv").exists()
    assert (tmp_path / "new.mkv").read_bytes() == b"data"


def test_apply_plan_moves_files_and_removes_empty_source_dir(tmp_path: Path) -> None:
    """Apply moves files to target dir (creating it) and removes empty source dir."""
    from anipyrenamer.cache import init_db

    old_dir = tmp_path / "OldDir"
    old_dir.mkdir()
    file_path = old_dir / "ep.mkv"
    file_path.write_bytes(b"video")
    new_dir = tmp_path / "NewDir"
    new_file_name = "renamed.mkv"
    db = tmp_path / "cache.sqlite"
    init_db(str(db))
    items = [
        RenameItem(str(file_path), str(new_dir / new_file_name), kind=RenameKind.FILE),
    ]
    apply_plan(items, str(db), dry_run=False)
    assert not file_path.exists()
    assert not old_dir.exists()
    assert new_dir.is_dir()
    assert (new_dir / new_file_name).exists()
    assert (new_dir / new_file_name).read_bytes() == b"video"


def test_apply_plan_ignores_skip_kind(tmp_path: Path) -> None:
    """SKIP items are not moved; only FILE items are applied."""
    src = tmp_path / "video.mkv"
    src.write_bytes(b"data")
    db = tmp_path / "cache.sqlite"
    from anipyrenamer.cache import init_db
    init_db(str(db))
    items = [
        RenameItem(str(src), "(AniDB lookup failed)", kind=RenameKind.SKIP),
    ]
    apply_plan(items, str(db), dry_run=False)
    assert src.exists()
    assert src.read_bytes() == b"data"


def test_apply_plan_skips_when_destination_exists(tmp_path: Path) -> None:
    """When destination already exists (and is not source), apply skips that item (no overwrite)."""
    from anipyrenamer.cache import init_db

    src = tmp_path / "orig.mkv"
    src.write_bytes(b"original")
    existing = tmp_path / "existing.mkv"
    existing.write_bytes(b"existing")
    db = tmp_path / "cache.sqlite"
    init_db(str(db))
    items = [RenameItem(str(src), str(existing), kind=RenameKind.FILE)]
    apply_plan(items, str(db), dry_run=False)
    assert src.exists()
    assert existing.read_bytes() == b"existing"
