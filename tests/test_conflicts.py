"""Tests for the conflicts module: destination-conflict analysis + policy application.

Behavior is pinned through the single public seam ``resolve_destination_conflicts``.
These characterize the behavior the extraction must preserve (formerly split across
``validation.analyze_destination_conflicts`` and ``cli._apply_suffix_conflict_resolution``).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from anipyrenamer.conflicts import ConflictResolution, resolve_destination_conflicts
from anipyrenamer.models import RenameItem, RenameKind


def _file(old: str, new: str) -> RenameItem:
    return RenameItem(old, new, kind=RenameKind.FILE)


# --- policy: skip / fail (no resolution; plan unchanged) ---------------------


def test_skip_no_conflicts_returns_plan_unchanged() -> None:
    plan = [_file("/a/x.mkv", "/b/y.mkv")]
    result = resolve_destination_conflicts(
        plan, policy="skip", strategy="none", case_insensitive=False
    )
    assert isinstance(result, ConflictResolution)
    assert [i.new_path for i in result.plan] == ["/b/y.mkv"]
    assert result.warnings == []
    assert result.conflict_indexes == frozenset()
    assert result.should_fail is False


def test_skip_planned_collision_reports_indexes_not_fail() -> None:
    plan = [_file("/a/one.mkv", "/dest/show-01.mkv"), _file("/a/two.mkv", "/dest/show-01.mkv")]
    result = resolve_destination_conflicts(
        plan, policy="skip", strategy="none", case_insensitive=False
    )
    assert result.conflict_indexes == frozenset({0, 1})
    assert any("Planned destination collision" in w for w in result.warnings)
    assert result.should_fail is False
    # skip never rewrites paths
    assert [i.new_path for i in result.plan] == ["/dest/show-01.mkv", "/dest/show-01.mkv"]


def test_fail_planned_collision_sets_should_fail() -> None:
    plan = [_file("/a/one.mkv", "/dest/same.mkv"), _file("/a/two.mkv", "/dest/same.mkv")]
    result = resolve_destination_conflicts(
        plan, policy="fail", strategy="none", case_insensitive=False
    )
    assert result.conflict_indexes == frozenset({0, 1})
    assert result.should_fail is True
    assert [i.new_path for i in result.plan] == ["/dest/same.mkv", "/dest/same.mkv"]


def test_fail_without_conflicts_does_not_fail() -> None:
    plan = [_file("/a/x.mkv", "/b/y.mkv")]
    result = resolve_destination_conflicts(
        plan, policy="fail", strategy="none", case_insensitive=False
    )
    assert result.conflict_indexes == frozenset()
    assert result.should_fail is False


# --- policy: suffix (resolution; relocated from test_cli.py) -----------------


def test_suffix_dedupes_planned_collisions() -> None:
    """Relocated from test_cli.py; now asserts on the returned plan, not in-place mutation."""
    plan = [_file("/in/a.mkv", "/dest/same.mkv"), _file("/in/b.mkv", "/dest/same.mkv")]
    result = resolve_destination_conflicts(
        plan, policy="suffix", strategy="counter", case_insensitive=False
    )
    assert result.plan[0].new_path == "/dest/same.mkv"
    assert result.plan[1].new_path.endswith("same (2).mkv")
    assert result.plan[0].new_path != result.plan[1].new_path
    assert result.should_fail is False


def test_suffix_dedupes_existing_destination(tmp_path: Path) -> None:
    """Relocated from test_cli.py; existing target on disk is rewritten."""
    src = tmp_path / "src.mkv"
    src.write_bytes(b"src")
    existing = tmp_path / "existing.mkv"
    existing.write_bytes(b"existing")
    plan = [_file(str(src), str(existing))]
    result = resolve_destination_conflicts(
        plan, policy="suffix", strategy="counter", case_insensitive=False
    )
    assert result.plan[0].new_path != str(existing)
    assert result.plan[0].new_path.endswith("existing (2).mkv")


def test_suffix_strategy_none_uses_dup_suffix() -> None:
    plan = [_file("/in/a.mkv", "/dest/same.mkv"), _file("/in/b.mkv", "/dest/same.mkv")]
    result = resolve_destination_conflicts(
        plan, policy="suffix", strategy="none", case_insensitive=False
    )
    assert result.plan[1].new_path.endswith("same-dup.mkv")


def test_suffix_strategy_hash_is_deterministic() -> None:
    plan = [_file("/in/a.mkv", "/dest/same.mkv"), _file("/in/b.mkv", "/dest/same.mkv")]
    r1 = resolve_destination_conflicts(plan, policy="suffix", strategy="hash", case_insensitive=False)
    r2 = resolve_destination_conflicts(plan, policy="suffix", strategy="hash", case_insensitive=False)
    digest = hashlib.sha1(b"/in/b.mkv").hexdigest()[:8]
    assert r1.plan[1].new_path.endswith(f"same-{digest}.mkv")
    assert r1.plan[1].new_path == r2.plan[1].new_path


# --- detection (relocated from test_validation.py) ---------------------------


def test_existing_destination_detected(tmp_path: Path) -> None:
    existing = tmp_path / "existing.mkv"
    existing.write_bytes(b"x")
    plan = [_file(str(tmp_path / "other.mkv"), str(existing))]
    result = resolve_destination_conflicts(
        plan, policy="skip", strategy="none", case_insensitive=False
    )
    assert len(result.warnings) == 1
    assert "already exists" in result.warnings[0]
    assert "will skip" in result.warnings[0]
    assert result.conflict_indexes == frozenset({0})


def test_planned_same_target_detected() -> None:
    plan = [_file("/a/one.mkv", "/dest/show-01.mkv"), _file("/a/two.mkv", "/dest/show-01.mkv")]
    result = resolve_destination_conflicts(
        plan, policy="skip", strategy="none", case_insensitive=False
    )
    assert any("Planned destination collision" in w for w in result.warnings)
    assert result.conflict_indexes == frozenset({0, 1})


def test_case_only_collision_detected_when_case_insensitive() -> None:
    """Relocated from test_validation.py."""
    plan = [_file("/a/one.mkv", "/dest/Show-01.mkv"), _file("/a/two.mkv", "/dest/show-01.mkv")]
    result = resolve_destination_conflicts(plan, policy="skip", strategy="none", case_insensitive=True)
    assert any("Planned destination collision" in w for w in result.warnings)
    assert result.conflict_indexes == frozenset({0, 1})


def test_case_only_not_a_conflict_when_case_sensitive() -> None:
    """The other case-insensitivity branch: distinct keys when case-sensitive."""
    plan = [_file("/a/one.mkv", "/dest/Show-01.mkv"), _file("/a/two.mkv", "/dest/show-01.mkv")]
    result = resolve_destination_conflicts(
        plan, policy="skip", strategy="none", case_insensitive=False
    )
    assert result.conflict_indexes == frozenset()
    assert result.warnings == []


def test_same_existing_path_is_not_a_conflict(tmp_path: Path) -> None:
    f = tmp_path / "f.mkv"
    f.write_bytes(b"x")
    plan = [_file(str(f), str(f))]  # source resolves to destination
    result = resolve_destination_conflicts(
        plan, policy="skip", strategy="none", case_insensitive=False
    )
    assert result.conflict_indexes == frozenset()
    assert result.warnings == []


# --- EXIT_PARTIAL equivalence: exact membership, incl. dedup cases -----------


def test_exit_partial_membership_one_existing_two_planned(tmp_path: Path) -> None:
    """One existing file targeted by 2 planned items: messages dedupe, indexes do not."""
    existing = tmp_path / "same.mkv"
    existing.write_bytes(b"x")
    plan = [
        _file(str(tmp_path / "a.mkv"), str(existing)),
        _file(str(tmp_path / "b.mkv"), str(existing)),
    ]
    result = resolve_destination_conflicts(
        plan, policy="skip", strategy="none", case_insensitive=False
    )
    assert result.conflict_indexes == frozenset({0, 1})
    # the equivalence the CLI relies on: non-emptiness matches, even though counts differ
    assert bool(result.conflict_indexes) == (len(result.warnings) > 0)


def test_exit_partial_membership_two_existing_one_each(tmp_path: Path) -> None:
    e1 = tmp_path / "e1.mkv"
    e1.write_bytes(b"x")
    e2 = tmp_path / "e2.mkv"
    e2.write_bytes(b"y")
    plan = [
        _file(str(tmp_path / "a.mkv"), str(e1)),
        _file(str(tmp_path / "b.mkv"), str(e2)),
    ]
    result = resolve_destination_conflicts(
        plan, policy="skip", strategy="none", case_insensitive=False
    )
    assert result.conflict_indexes == frozenset({0, 1})
    assert len(result.warnings) == 2
    assert bool(result.conflict_indexes) == (len(result.warnings) > 0)


# --- contract: return-new, idempotence, strategy scoping ---------------------


def test_return_new_does_not_mutate_input() -> None:
    plan = [_file("/in/a.mkv", "/dest/same.mkv"), _file("/in/b.mkv", "/dest/same.mkv")]
    result = resolve_destination_conflicts(
        plan, policy="suffix", strategy="counter", case_insensitive=False
    )
    # original list and items are untouched
    assert plan[0].new_path == "/dest/same.mkv"
    assert plan[1].new_path == "/dest/same.mkv"
    # output is a distinct list with the resolved path
    assert result.plan is not plan
    assert result.plan[1].new_path.endswith("same (2).mkv")


def test_idempotent_repeated_calls() -> None:
    plan = [_file("/in/a.mkv", "/dest/same.mkv"), _file("/in/b.mkv", "/dest/same.mkv")]
    r1 = resolve_destination_conflicts(plan, policy="suffix", strategy="counter", case_insensitive=False)
    r2 = resolve_destination_conflicts(plan, policy="suffix", strategy="counter", case_insensitive=False)
    assert r1 == r2


def test_strategy_ignored_under_skip() -> None:
    plan = [_file("/in/a.mkv", "/dest/same.mkv"), _file("/in/b.mkv", "/dest/same.mkv")]
    result = resolve_destination_conflicts(
        plan, policy="skip", strategy="hash", case_insensitive=False
    )
    # skip never rewrites, regardless of strategy
    assert [i.new_path for i in result.plan] == ["/dest/same.mkv", "/dest/same.mkv"]
    assert result.conflict_indexes == frozenset({0, 1})


def test_non_file_items_preserved_under_suffix() -> None:
    """SKIP items survive resolution in place (order + identity)."""
    skip = RenameItem("/in/u.mkv", "(AniDB lookup failed)", kind=RenameKind.SKIP)
    plan = [skip, _file("/in/a.mkv", "/dest/same.mkv"), _file("/in/b.mkv", "/dest/same.mkv")]
    result = resolve_destination_conflicts(
        plan, policy="suffix", strategy="counter", case_insensitive=False
    )
    assert result.plan[0] is skip
    assert result.plan[2].new_path.endswith("same (2).mkv")
