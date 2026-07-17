"""Tests for apply. (Plan preview rendering moved to the ledger — see test_ledger.py, #51.)"""

from __future__ import annotations
from pathlib import Path

import pytest

from anipyrenamer.apply import apply_plan
from anipyrenamer.models import RenameItem, RenameKind


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


def test_apply_plan_returns_skip_reason_breakdown(tmp_path: Path) -> None:
    """apply_plan reports WHY items were skipped: destination-exists vs source-missing (SPEC §5).

    The breakdown reconciles: applied + skipped_destination_exists +
    skipped_source_missing == attempted FILE items.
    """
    from anipyrenamer.cache import init_db

    movable = tmp_path / "movable.mkv"
    movable.write_bytes(b"a")
    blocked = tmp_path / "blocked.mkv"
    blocked.write_bytes(b"b")
    occupied = tmp_path / "occupied.mkv"
    occupied.write_bytes(b"existing")
    gone = tmp_path / "gone.mkv"  # never created: source missing
    db = tmp_path / "cache.sqlite"
    init_db(str(db))
    items = [
        RenameItem(str(movable), str(tmp_path / "moved.mkv"), kind=RenameKind.FILE),
        RenameItem(str(blocked), str(occupied), kind=RenameKind.FILE),
        RenameItem(str(gone), str(tmp_path / "never.mkv"), kind=RenameKind.FILE),
    ]
    result = apply_plan(items, str(db), dry_run=False)
    assert result.applied == 1
    assert result.skipped_destination_exists == 1
    assert result.skipped_source_missing == 1
    assert result.skipped_total == 2
    assert result.applied + result.skipped_total == 3


def test_apply_plan_dry_run_breakdown_is_all_zero(tmp_path: Path) -> None:
    """dry_run returns a zeroed breakdown and performs no moves."""
    src = tmp_path / "orig.mkv"
    src.write_bytes(b"data")
    items = [RenameItem(str(src), str(tmp_path / "new.mkv"))]
    result = apply_plan(items, str(tmp_path / "db.sqlite"), dry_run=True)
    assert (result.applied, result.skipped_destination_exists, result.skipped_source_missing) == (
        0,
        0,
        0,
    )
    assert src.exists()


def test_apply_plan_chdir_parent_when_cwd_matches_source_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When cwd is the emptied source dir, apply moves cwd to parent before removal."""
    from anipyrenamer.cache import init_db

    old_dir = tmp_path / "OldDir"
    old_dir.mkdir()
    src = old_dir / "video.mkv"
    src.write_bytes(b"data")
    new_dir = tmp_path / "NewDir"
    db = tmp_path / "cache.sqlite"
    init_db(str(db))
    monkeypatch.chdir(old_dir)

    items = [RenameItem(str(src), str(new_dir / "video.mkv"), kind=RenameKind.FILE)]
    apply_plan(items, str(db), dry_run=False)

    assert not old_dir.exists()
    assert Path.cwd() == tmp_path
    assert (new_dir / "video.mkv").exists()


def test_apply_plan_cleanup_permission_error_is_non_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Locked/permission errors during source-dir cleanup are logged and do not abort apply."""
    from anipyrenamer.cache import init_db

    old_dir = tmp_path / "OldDir"
    old_dir.mkdir()
    src = old_dir / "video.mkv"
    src.write_bytes(b"data")
    new_dir = tmp_path / "NewDir"
    db = tmp_path / "cache.sqlite"
    init_db(str(db))

    original_rmdir = Path.rmdir

    def _patched_rmdir(path: Path) -> None:
        if path.resolve() == old_dir.resolve():
            raise PermissionError("[WinError 32] simulated lock")
        original_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", _patched_rmdir)

    items = [RenameItem(str(src), str(new_dir / "video.mkv"), kind=RenameKind.FILE)]
    apply_plan(items, str(db), dry_run=False)

    assert old_dir.exists()
    assert (new_dir / "video.mkv").exists()
    assert any("Skipping source directory cleanup" in rec.message for rec in caplog.records)
