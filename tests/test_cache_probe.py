"""Tests for the #40 bounded, non-persistent cache operational probe (SPEC.md §8 Check 4)."""

from __future__ import annotations

import errno
import sqlite3
from pathlib import Path

import pytest

from anipyrenamer.cache import CacheProbeOutcome, probe_cache_operational


def test_probe_missing_db_existing_parent_is_pass_and_leaves_no_residue(tmp_path: Path) -> None:
    """Deep-minimal probe: SQLite creates a uniquely-named temp DB, writes, cleans up."""
    db_path = tmp_path / "cache.sqlite"
    result = probe_cache_operational(str(db_path))

    assert result.outcome == CacheProbeOutcome.OPERATIONAL
    assert not db_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_probe_missing_db_does_not_precreate_with_mkstemp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe never pre-creates the temp DB file itself; SQLite must create it."""
    import tempfile

    def forbidden_mkstemp(*args: object, **kwargs: object) -> None:
        raise AssertionError("probe must not pre-create the temp DB with mkstemp")

    monkeypatch.setattr(tempfile, "mkstemp", forbidden_mkstemp)
    db_path = tmp_path / "cache.sqlite"
    result = probe_cache_operational(str(db_path))
    assert result.outcome == CacheProbeOutcome.OPERATIONAL


def test_probe_missing_db_missing_parent_uses_throwaway_ancestor_tree(tmp_path: Path) -> None:
    """Missing parents: probe under nearest existing ancestor, never creates the real path."""
    db_path = tmp_path / "a" / "b" / "c" / "cache.sqlite"
    result = probe_cache_operational(str(db_path))

    assert result.outcome == CacheProbeOutcome.OPERATIONAL
    assert "nearest existing ancestor" in result.detail
    assert not db_path.parent.exists()
    assert list(tmp_path.iterdir()) == []


def test_probe_missing_db_nonwritable_parent_is_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-writable parent (OS-code classified) fails the probe."""
    db_path = tmp_path / "cache.sqlite"

    def raise_readonly(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("unable to open database file")

    import anipyrenamer.cache as cache_mod

    monkeypatch.setattr(cache_mod.sqlite3, "connect", raise_readonly)
    result = probe_cache_operational(str(db_path))
    assert result.outcome == CacheProbeOutcome.UNUSABLE


def test_probe_missing_db_missing_ancestor_tree_is_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the throwaway ancestor probe cannot create its tree, the probe fails."""
    db_path = tmp_path / "a" / "b" / "cache.sqlite"

    def raise_denied(*args: object, **kwargs: object) -> None:
        err = OSError()
        err.errno = errno.EACCES
        raise err

    import anipyrenamer.cache as cache_mod

    monkeypatch.setattr(cache_mod.Path, "mkdir", raise_denied)
    result = probe_cache_operational(str(db_path))
    assert result.outcome == CacheProbeOutcome.UNUSABLE


def test_probe_throwaway_tree_cleaned_up_even_on_db_probe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Throwaway ancestor-tree directories are removed even when the DB probe itself errors.

    Cleanup runs in a ``finally`` around the whole probe, not only on the
    success path, so a mid-probe failure never leaves the throwaway tree behind.
    """
    import anipyrenamer.cache as cache_mod

    db_path = tmp_path / "a" / "b" / "cache.sqlite"

    def raise_readonly(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(cache_mod.sqlite3, "connect", raise_readonly)
    result = probe_cache_operational(str(db_path))

    assert result.outcome == CacheProbeOutcome.UNUSABLE
    assert list(tmp_path.iterdir()) == []


def test_probe_existing_valid_db_acquires_and_releases_write_lock(tmp_path: Path) -> None:
    """Existing DB: read-only schema check, then guarded BEGIN IMMEDIATE / ROLLBACK."""
    db_path = tmp_path / "cache.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()

    result = probe_cache_operational(str(db_path))

    assert result.outcome == CacheProbeOutcome.OPERATIONAL
    # Non-persistence: no data/schema mutation beyond what already existed.
    with sqlite3.connect(db_path) as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert tables == [("t",)]


def test_probe_existing_db_never_calls_init_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe must never call init_db against the configured path."""
    import anipyrenamer.cache as cache_mod

    db_path = tmp_path / "cache.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()

    def forbidden_init_db(*args: object, **kwargs: object) -> None:
        raise AssertionError("probe must never call init_db")

    monkeypatch.setattr(cache_mod, "init_db", forbidden_init_db)
    result = probe_cache_operational(str(db_path))
    assert result.outcome == CacheProbeOutcome.OPERATIONAL


def test_probe_existing_db_pending_rollback_journal_is_warn(tmp_path: Path) -> None:
    """A non-empty rollback journal sidecar is a recovery risk: guarded warn, no R/W open."""
    db_path = tmp_path / "cache.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()
    journal = tmp_path / "cache.sqlite-journal"
    journal.write_bytes(b"\x00" * 64)

    result = probe_cache_operational(str(db_path))

    assert result.outcome == CacheProbeOutcome.INCONCLUSIVE
    assert journal.exists()  # doctor never touches the operator's real journal


def test_probe_existing_db_readonly_file_is_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SQLITE_READONLY on the real file is fail, classified by SQLite code not message text."""
    db_path = tmp_path / "cache.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()

    import anipyrenamer.cache as cache_mod

    real_connect = sqlite3.connect

    def readonly_on_rw(path: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        if "mode=ro" in str(path):
            return real_connect(path, *args, **kwargs)  # type: ignore[arg-type]
        err = sqlite3.OperationalError("attempt to write a readonly database")
        err.sqlite_errorcode = 1032  # SQLITE_READONLY
        raise err

    monkeypatch.setattr(cache_mod.sqlite3, "connect", readonly_on_rw)
    result = probe_cache_operational(str(db_path))
    assert result.outcome == CacheProbeOutcome.UNUSABLE


def test_probe_existing_db_malformed_is_fail(tmp_path: Path) -> None:
    """A malformed SQLite file fails the read-only schema validation."""
    db_path = tmp_path / "cache.sqlite"
    db_path.write_bytes(b"not a sqlite database")

    result = probe_cache_operational(str(db_path))
    assert result.outcome == CacheProbeOutcome.UNUSABLE


def test_probe_existing_db_busy_lock_is_warn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SQLITE_BUSY / lock timeout is inconclusive contention, not proof of unusability."""
    db_path = tmp_path / "cache.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()

    import anipyrenamer.cache as cache_mod

    real_connect = sqlite3.connect

    def busy_on_rw(path: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        if "mode=ro" in str(path):
            return real_connect(path, *args, **kwargs)  # type: ignore[arg-type]
        err = sqlite3.OperationalError("database is locked")
        err.sqlite_errorcode = 5  # SQLITE_BUSY
        raise err

    monkeypatch.setattr(cache_mod.sqlite3, "connect", busy_on_rw)
    result = probe_cache_operational(str(db_path))
    assert result.outcome == CacheProbeOutcome.INCONCLUSIVE


def test_probe_classifies_by_code_not_message_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Classification must use sqlite_errorcode/errno/winerror, never string matching."""
    db_path = tmp_path / "cache.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()

    import anipyrenamer.cache as cache_mod

    real_connect = sqlite3.connect

    def misleading_message(path: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        if "mode=ro" in str(path):
            return real_connect(path, *args, **kwargs)  # type: ignore[arg-type]
        # Message text says "locked" but the actual code is SQLITE_READONLY (fail),
        # proving the implementation trusts the code, not the string.
        err = sqlite3.OperationalError("database is locked")
        err.sqlite_errorcode = 1032  # SQLITE_READONLY
        raise err

    monkeypatch.setattr(cache_mod.sqlite3, "connect", misleading_message)
    result = probe_cache_operational(str(db_path))
    assert result.outcome == CacheProbeOutcome.UNUSABLE


def test_probe_cleanup_residue_dominates_to_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unremovable temp artifact overrides an otherwise-successful probe."""
    import anipyrenamer.cache as cache_mod

    db_path = tmp_path / "cache.sqlite"
    real_unlink = Path.unlink

    def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self.suffix == ".sqlite" and "doctor" in self.name:
            raise OSError("cannot remove")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(cache_mod.Path, "unlink", failing_unlink)
    result = probe_cache_operational(str(db_path))
    assert result.outcome == CacheProbeOutcome.UNUSABLE
    assert "cleanup" in result.detail.lower() or "residue" in result.detail.lower()


def test_probe_cleanup_detail_names_which_step_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cleanup-residue detail names the artifact category, not just the path."""
    import anipyrenamer.cache as cache_mod

    db_path = tmp_path / "cache.sqlite"
    real_unlink = Path.unlink

    def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self.suffix == ".sqlite" and "doctor" in self.name:
            raise OSError("cannot remove")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(cache_mod.Path, "unlink", failing_unlink)
    result = probe_cache_operational(str(db_path))
    assert "temporary probe database" in result.detail


def test_probe_already_absent_artifact_is_not_cleanup_failure(tmp_path: Path) -> None:
    """An artifact that is already gone before cleanup is not treated as a failure."""
    db_path = tmp_path / "cache.sqlite"
    result = probe_cache_operational(str(db_path))
    assert result.outcome == CacheProbeOutcome.OPERATIONAL


def test_probe_detail_is_sanitized_names_and_paths_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Detail text carries only paths/categories, never raw exception text."""
    db_path = tmp_path / "cache.sqlite"

    import anipyrenamer.cache as cache_mod

    def raise_with_secret(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("secret-token-should-not-leak: disk quota exceeded")

    monkeypatch.setattr(cache_mod.sqlite3, "connect", raise_with_secret)
    result = probe_cache_operational(str(db_path))
    assert "secret-token-should-not-leak" not in result.detail
