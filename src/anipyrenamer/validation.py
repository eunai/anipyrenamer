"""Validate and flatten rename plans: flatten file items only; no implicit overwrite.

Destination-conflict detection and policy now live in ``anipyrenamer.conflicts``.
"""

from __future__ import annotations

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
