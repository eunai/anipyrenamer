"""Validate and flatten rename plans: flatten file items only; no implicit overwrite."""

from __future__ import annotations

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


def detect_destination_conflicts(items: list[RenameItem]) -> list[str]:
    """
    Check for existing destinations (no implicit overwrite; apply will skip these).
    Returns list of human-readable conflict messages.
    """
    conflicts: list[str] = []
    seen: set[str] = set()

    for item in items:
        if item.kind != RenameKind.FILE:
            continue
        dst = Path(item.new_path)
        if not dst.exists():
            continue
        src = Path(item.old_path)
        if src.exists() and _same_path(src, dst):
            continue
        key = dst.resolve().as_posix()
        if key not in seen:
            seen.add(key)
            conflicts.append(f"Destination already exists: {item.new_path}; will skip.")

    return conflicts
