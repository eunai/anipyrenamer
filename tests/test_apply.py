"""Tests for apply. (Plan preview rendering moved to the ledger — see test_ledger.py, #51.)"""

from __future__ import annotations

import shutil
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


def test_apply_plan_move_oserror_is_skipped_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A move-time OSError (e.g. Windows file-in-use) does not crash apply_plan; it is
    counted as skipped_apply_failed and the batch continues (issue #54)."""
    from anipyrenamer.cache import init_db

    locked = tmp_path / "locked.mkv"
    locked.write_bytes(b"locked")
    movable = tmp_path / "movable.mkv"
    movable.write_bytes(b"movable")
    db = tmp_path / "cache.sqlite"
    init_db(str(db))

    original_move = shutil.move

    def _patched_move(src: str, dst: str) -> str:
        if Path(src) == locked:
            raise PermissionError(
                "[WinError 32] The process cannot access the file "
                "because it is being used by another process"
            )
        return original_move(src, dst)

    monkeypatch.setattr(shutil, "move", _patched_move)

    items = [
        RenameItem(str(locked), str(tmp_path / "renamed_locked.mkv"), kind=RenameKind.FILE),
        RenameItem(str(movable), str(tmp_path / "renamed_movable.mkv"), kind=RenameKind.FILE),
    ]
    result = apply_plan(items, str(db), dry_run=False)

    assert locked.exists()  # untouched by the failed move
    assert not movable.exists()
    assert (tmp_path / "renamed_movable.mkv").exists()  # batch continued past the failure
    assert result.applied == 1
    assert result.skipped_apply_failed == 1
    assert result.skipped_total == 1


def _lock_violation(winerror: int | None) -> OSError:
    """An OSError tagged with a Windows winerror, as a real sharing/lock violation
    would be — winerror is not derivable from the message text (issue #55)."""
    exc = OSError("[WinError %s] simulated lock" % winerror if winerror else "simulated denial")
    exc.winerror = winerror  # type: ignore[attr-defined]
    return exc


def _new_src_db_and_sleep_recorder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str, list[float]]:
    """Shared src-file/db/sleep-recorder setup for the retry scenario tests below.
    Each test still builds its own dst, injects its own shutil.move/mkdir failure,
    and makes its own assertions (issue #55)."""
    from anipyrenamer import apply as apply_module
    from anipyrenamer.cache import init_db

    src = tmp_path / "orig.mkv"
    src.write_bytes(b"data")
    db = tmp_path / "cache.sqlite"
    init_db(str(db))

    sleeps: list[float] = []
    monkeypatch.setattr(apply_module.time, "sleep", lambda s: sleeps.append(s))

    return src, str(db), sleeps


def test_apply_plan_retries_transient_lock_violation_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A winerror=32 sharing violation is retried (bounded budget) and can still
    succeed once the lock clears, with the documented 250ms/500ms backoff (issue #55)."""
    src, db, sleeps = _new_src_db_and_sleep_recorder(tmp_path, monkeypatch)
    dst = tmp_path / "new.mkv"

    original_move = shutil.move
    calls = {"n": 0}

    def _flaky_move(s: str, d: str) -> str:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _lock_violation(32)
        return original_move(s, d)

    monkeypatch.setattr(shutil, "move", _flaky_move)

    items = [RenameItem(str(src), str(dst), kind=RenameKind.FILE)]
    result = apply_plan(items, db, dry_run=False)

    assert calls["n"] == 3  # initial attempt + 2 retries
    assert sleeps == [0.25, 0.5]
    assert not src.exists()
    assert dst.exists()
    assert result.applied == 1
    assert result.skipped_apply_failed == 0


def test_apply_plan_retries_exhausted_lands_in_apply_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A winerror=32 violation that never clears still gives up after the fixed
    retry budget and lands in skipped_apply_failed exactly as a non-retried
    failure would — no behavior change beyond the timing (issue #55)."""
    src, db, sleeps = _new_src_db_and_sleep_recorder(tmp_path, monkeypatch)
    dst = tmp_path / "new.mkv"

    calls = {"n": 0}

    def _always_locked(s: str, d: str) -> str:
        calls["n"] += 1
        raise _lock_violation(32)

    monkeypatch.setattr(shutil, "move", _always_locked)

    items = [RenameItem(str(src), str(dst), kind=RenameKind.FILE)]
    result = apply_plan(items, db, dry_run=False)

    assert calls["n"] == 3  # initial attempt + 2 retries, then gives up
    assert sleeps == [0.25, 0.5]
    assert src.exists()
    assert not dst.exists()
    assert result.applied == 0
    assert result.skipped_apply_failed == 1
    assert result.failures[0].dst_exists_after is False


def test_apply_plan_non_lock_oserror_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OSError without a winerror 32/33 signal (e.g. a plain PermissionError from
    a read-only target, or a disk-full/invalid-path failure) is not retried — it
    falls straight through to skipped_apply_failed on the first attempt (issue #55)."""
    src, db, sleeps = _new_src_db_and_sleep_recorder(tmp_path, monkeypatch)
    dst = tmp_path / "new.mkv"

    calls = {"n": 0}

    def _access_denied(s: str, d: str) -> str:
        calls["n"] += 1
        raise _lock_violation(5)  # ERROR_ACCESS_DENIED, not a sharing/lock violation

    monkeypatch.setattr(shutil, "move", _access_denied)

    items = [RenameItem(str(src), str(dst), kind=RenameKind.FILE)]
    result = apply_plan(items, db, dry_run=False)

    assert calls["n"] == 1  # no retry attempted
    assert sleeps == []
    assert result.applied == 0
    assert result.skipped_apply_failed == 1


def test_apply_plan_mkdir_lock_style_oserror_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retry is scoped to the move step only: even a winerror=32-tagged mkdir
    failure is never retried, since a denied/missing parent directory is not a
    transient lock (issue #55)."""
    src, db, sleeps = _new_src_db_and_sleep_recorder(tmp_path, monkeypatch)
    dst = tmp_path / "NewDir" / "new.mkv"

    calls = {"n": 0}
    original_mkdir = Path.mkdir

    def _patched_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self == dst.parent:
            calls["n"] += 1
            raise _lock_violation(32)
        original_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", _patched_mkdir)

    items = [RenameItem(str(src), str(dst), kind=RenameKind.FILE)]
    result = apply_plan(items, db, dry_run=False)

    assert calls["n"] == 1  # no retry attempted on mkdir, even for a lock-style OSError
    assert sleeps == []
    assert result.applied == 0
    assert result.skipped_apply_failed == 1


def test_apply_plan_mkdir_oserror_is_skipped_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A parent-dir creation OSError is caught the same as a move OSError: the failed
    item is counted apply-failed, not applied, and the batch continues to the next
    item (issue #54)."""
    from anipyrenamer.cache import init_db

    failing_src = tmp_path / "orig1.mkv"
    failing_src.write_bytes(b"data1")
    movable_src = tmp_path / "orig2.mkv"
    movable_src.write_bytes(b"data2")
    db = tmp_path / "cache.sqlite"
    init_db(str(db))
    failing_dst = tmp_path / "FailDir" / "new1.mkv"
    movable_dst = tmp_path / "OkDir" / "new2.mkv"

    original_mkdir = Path.mkdir

    def _patched_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self == failing_dst.parent:
            raise PermissionError("[WinError 5] Access is denied")
        original_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", _patched_mkdir)

    items = [
        RenameItem(str(failing_src), str(failing_dst), kind=RenameKind.FILE),
        RenameItem(str(movable_src), str(movable_dst), kind=RenameKind.FILE),
    ]
    with caplog.at_level("WARNING"):
        result = apply_plan(items, str(db), dry_run=False)

    assert failing_src.exists()  # never touched: mkdir failed before shutil.move ran
    assert not failing_dst.exists()
    assert not movable_src.exists()  # batch continued past the failure
    assert movable_dst.exists()
    assert result.applied == 1
    assert result.skipped_apply_failed == 1
    assert len(result.failures) == 1
    assert result.failures[0].dst_exists_after is False
    assert any("Apply failed" in rec.message for rec in caplog.records)


def test_apply_plan_move_failure_after_destination_written_flags_manual_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Reproduces the actual reported traceback's shape: shutil.move's copy-then-remove
    fallback writes the destination, then fails removing the (still-locked) source.
    No rollback is attempted; the escalated evidence flags manual review (issue #54)."""
    from anipyrenamer.cache import init_db

    src = tmp_path / "orig.mkv"
    src.write_bytes(b"original content")
    db = tmp_path / "cache.sqlite"
    init_db(str(db))
    dst = tmp_path / "new.mkv"

    def _fake_move(s: str, d: str) -> str:
        # Simulates shutil.move's fallback: copy succeeds, then os.unlink(src) fails.
        Path(d).write_bytes(Path(s).read_bytes())
        raise PermissionError(
            "[WinError 32] The process cannot access the file "
            "because it is being used by another process"
        )

    monkeypatch.setattr(shutil, "move", _fake_move)

    items = [RenameItem(str(src), str(dst), kind=RenameKind.FILE)]
    with caplog.at_level("WARNING"):
        result = apply_plan(items, str(db), dry_run=False)

    # No rollback: both the source and the (fully-written) destination remain on disk.
    assert src.exists()
    assert src.read_bytes() == b"original content"
    assert dst.exists()
    assert dst.read_bytes() == b"original content"
    assert result.applied == 0
    assert result.skipped_apply_failed == 1
    assert result.skipped_total == 1
    failure = result.failures[0]
    assert failure.src_exists_after is True
    assert failure.dst_exists_after is True
    assert any(
        rec.levelname == "ERROR" and "manual" in rec.message.lower() for rec in caplog.records
    )


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
