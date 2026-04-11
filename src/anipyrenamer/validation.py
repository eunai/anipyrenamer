"""Validate and flatten rename plans: flatten file items only; no implicit overwrite."""

from __future__ import annotations

import os
from pathlib import Path

from anipyrenamer.models import RenameItem, RenameKind


def flatten_and_validate_folder_renames(
    all_items: list[tuple[list[RenameItem], str]],
) -> tuple[list[RenameItem], list[str]]:
    """
    Flatten per-group renames to file and skip items.

    - File items: all included, deduplicated by (old_path, new_path).
    - Skip items: included so they appear in the plan table (AniDB lookup failed).
    - Directory items: ignored (plan no longer emits them; apply uses file moves + empty-dir cleanup).

    Returns (flat list of RenameItems for display/apply; only FILE are applied, empty list of conflict messages).
    """
    flat: list[RenameItem] = []
    seen_file_keys: set[tuple[str, str]] = set()

    for items, _ in all_items:
        for item in items:
            if item.kind in (RenameKind.FILE, RenameKind.SKIP):
                key = (item.old_path, item.new_path)
                if key not in seen_file_keys:
                    seen_file_keys.add(key)
                    flat.append(item)

    return flat, []


def _same_path(a: Path, b: Path) -> bool:
    """True if both paths exist and refer to the same file/dir."""
    if not a.exists() or not b.exists():
        return False
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _dest_key(path_str: str, *, case_insensitive: bool) -> str:
    p = Path(path_str)
    try:
        key = p.resolve().as_posix() if p.exists() else p.as_posix()
    except OSError:
        key = p.as_posix()
    if case_insensitive:
        return key.casefold()
    return key


def analyze_destination_conflicts(
    items: list[RenameItem],
    *,
    case_insensitive: bool | None = None,
) -> tuple[list[str], set[int]]:
    """
    Analyze destination conflicts.

    Detects:
    - Existing destination conflicts on disk.
    - Planned collisions where multiple FILE items target the same destination
      (including case-only collisions on case-insensitive filesystems).

    Returns (messages, conflicting_item_indexes).
    """
    if case_insensitive is None:
        case_insensitive = os.name == "nt"

    conflicts: list[str] = []
    conflict_indexes: set[int] = set()
    seen_existing_keys: set[str] = set()
    planned_targets: dict[str, list[int]] = {}

    for idx, item in enumerate(items):
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
        first = items[indexes[0]]
        conflicts.append(
            f"Planned destination collision: {first.new_path}; multiple files target this path; will skip."
        )
        conflict_indexes.update(indexes)

    return conflicts, conflict_indexes


def detect_destination_conflicts(items: list[RenameItem]) -> list[str]:
    """
    Check for existing destinations (no implicit overwrite; apply will skip these).
    Returns list of human-readable conflict messages.
    """
    conflicts, _ = analyze_destination_conflicts(items)
    return conflicts
