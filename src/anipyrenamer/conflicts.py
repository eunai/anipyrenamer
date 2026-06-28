"""Destination-conflict analysis and policy application for the rename pipeline.

One deep interface: given a flattened rename plan plus the conflict policy and the
name-dedupe strategy, return a conflict-free plan, the warning messages, the
conflicting indexes (into the returned plan), and whether the caller should fail.

Contract: the input ``plan`` and the original ``RenameItem`` objects are never
mutated. Under ``suffix`` policy, changed entries are rebuilt with
``dataclasses.replace`` and returned in a new list; input order is preserved.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from anipyrenamer.models import RenameItem, RenameKind

ConflictPolicy = Literal["skip", "suffix", "fail"]
DedupeStrategy = Literal["none", "counter", "hash"]


@dataclass(frozen=True)
class ConflictResolution:
    """Outcome of applying a conflict policy to a plan.

    - ``plan``: the resulting plan (paths unchanged for ``skip``/``fail``;
      suffix-resolved for ``suffix``). Always a new list; never the input object.
    - ``warnings``: human-readable conflict messages for the CLI to render.
    - ``conflict_indexes``: indexes into ``plan`` whose destinations still conflict.
    - ``should_fail``: True only when ``policy == "fail"`` and conflicts remain.
    """

    plan: list[RenameItem]
    warnings: list[str]
    conflict_indexes: frozenset[int]
    should_fail: bool


def _dest_key(path_str: str, *, case_insensitive: bool) -> str:
    p = Path(path_str)
    try:
        key = p.resolve().as_posix() if p.exists() else p.as_posix()
    except OSError:
        key = p.as_posix()
    return key.casefold() if case_insensitive else key


def _same_path(a: Path, b: Path) -> bool:
    """True when both paths exist and resolve to the same filesystem entry."""
    if not a.exists() or not b.exists():
        return False
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _deduped_path(dst: Path, *, strategy: str, old_path: str, attempt: int) -> Path:
    stem = dst.stem
    suffix = dst.suffix
    if strategy == "hash":
        digest = hashlib.sha1(old_path.encode("utf-8")).hexdigest()[:8]
        candidate_stem = f"{stem}-{digest}" if attempt == 1 else f"{stem}-{digest}-{attempt}"
    elif strategy == "counter":
        candidate_stem = f"{stem} ({attempt + 1})"
    else:
        candidate_stem = stem + "-dup" if attempt == 1 else f"{stem}-dup-{attempt}"
    return dst.with_name(candidate_stem + suffix)


def _resolve_suffix(
    plan: list[RenameItem], *, strategy: str, case_insensitive: bool
) -> list[RenameItem]:
    """Return a NEW plan with suffix-deduped FILE paths; the input is not mutated.

    Path containment was already validated in ``build_plan``; ``_deduped_path`` uses
    ``Path.with_name`` which changes only the final component, preserving the parent
    directory and therefore the containment guarantee (SEC-05 / P2-A).
    """
    reserved: set[str] = set()
    out: list[RenameItem] = []
    for item in plan:
        if item.kind != RenameKind.FILE:
            out.append(item)
            continue

        src = Path(item.old_path)
        dst = Path(item.new_path)
        key = _dest_key(item.new_path, case_insensitive=case_insensitive)
        exists_conflict = dst.exists() and not _same_path(src, dst)
        if not exists_conflict and key not in reserved:
            reserved.add(key)
            out.append(item)
            continue

        resolved = False
        for attempt in range(1, 256):
            candidate = _deduped_path(
                dst, strategy=strategy, old_path=item.old_path, attempt=attempt
            )
            candidate_key = _dest_key(str(candidate), case_insensitive=case_insensitive)
            candidate_conflict = candidate.exists() and not _same_path(src, candidate)
            if candidate_conflict or candidate_key in reserved:
                continue
            out.append(replace(item, new_path=str(candidate)))
            reserved.add(candidate_key)
            resolved = True
            break

        if not resolved:
            reserved.add(key)
            out.append(item)

    return out


def _analyze(plan: list[RenameItem], *, case_insensitive: bool) -> tuple[list[str], set[int]]:
    """Detect destination conflicts in ``plan``.

    Detects existing-destination conflicts on disk and planned collisions where
    multiple FILE items target the same destination (including case-only collisions
    on case-insensitive filesystems). Returns (messages, conflicting indexes).
    """
    conflicts: list[str] = []
    conflict_indexes: set[int] = set()
    seen_existing_keys: set[str] = set()
    planned_targets: dict[str, list[int]] = {}

    for idx, item in enumerate(plan):
        if item.kind != RenameKind.FILE:
            continue

        dst = Path(item.new_path)
        key = _dest_key(item.new_path, case_insensitive=case_insensitive)
        planned_targets.setdefault(key, []).append(idx)

        if not dst.exists():
            continue
        src = Path(item.old_path)
        if src.exists() and _same_path(src, dst):
            continue
        if key not in seen_existing_keys:
            seen_existing_keys.add(key)
            conflicts.append(f"Destination already exists: {item.new_path}; will skip.")
        conflict_indexes.add(idx)

    for indexes in planned_targets.values():
        if len(indexes) <= 1:
            continue
        first = plan[indexes[0]]
        conflicts.append(
            f"Planned destination collision: {first.new_path}; "
            "multiple files target this path; will skip."
        )
        conflict_indexes.update(indexes)

    return conflicts, conflict_indexes


def resolve_destination_conflicts(
    plan: list[RenameItem],
    *,
    policy: ConflictPolicy,
    strategy: DedupeStrategy,
    case_insensitive: bool | None = None,
) -> ConflictResolution:
    """Apply ``policy`` to ``plan`` and report the outcome.

    - ``skip`` / ``fail``: no resolution; the returned plan keeps the input paths.
      ``conflict_indexes`` / ``warnings`` cover all detected conflicts. ``should_fail``
      is True only for ``fail`` when conflicts exist.
    - ``suffix``: collisions are deduped with ``strategy`` into a new plan; the
      returned ``conflict_indexes`` / ``warnings`` reflect any residual conflicts.

    ``case_insensitive`` defaults to the platform (``os.name == "nt"``) and is
    injectable for tests. ``strategy`` is consulted only under ``suffix``.
    """
    if case_insensitive is None:
        case_insensitive = os.name == "nt"

    resolved_plan = (
        _resolve_suffix(plan, strategy=strategy, case_insensitive=case_insensitive)
        if policy == "suffix"
        else list(plan)
    )

    warnings, conflict_indexes = _analyze(resolved_plan, case_insensitive=case_insensitive)
    should_fail = policy == "fail" and bool(conflict_indexes)

    return ConflictResolution(
        plan=resolved_plan,
        warnings=warnings,
        conflict_indexes=frozenset(conflict_indexes),
        should_fail=should_fail,
    )
