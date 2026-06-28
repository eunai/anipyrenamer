"""Tests for validation: folder conflict detection and destination conflicts."""

from __future__ import annotations

from anipyrenamer.models import RenameItem, RenameKind
from anipyrenamer.validation import flatten_and_validate_folder_renames


def test_flatten_and_validate_folder_renames_single_target_per_folder() -> None:
    """Flatten returns only file items; directory items are ignored; no conflicts."""
    items1 = [
        RenameItem("/root/Anime/ep01.mkv", "/root/Show [Subs]/Show - 01.mkv", kind=RenameKind.FILE),
        RenameItem("/root/Anime", "/root/Show [Subs]", kind=RenameKind.DIRECTORY),
    ]
    items2 = [
        RenameItem("/root/Anime/ep02.mkv", "/root/Show [Subs]/Show - 02.mkv", kind=RenameKind.FILE),
        RenameItem("/root/Anime", "/root/Show [Subs]", kind=RenameKind.DIRECTORY),
    ]
    flat, conflicts = flatten_and_validate_folder_renames([(items1, ""), (items2, "")])
    assert len(conflicts) == 0
    file_items = [i for i in flat if i.kind == RenameKind.FILE]
    folder_items = [i for i in flat if i.kind == RenameKind.DIRECTORY]
    assert len(file_items) == 2
    assert len(folder_items) == 0


def test_flatten_includes_skip_items() -> None:
    """Flatten includes SKIP items so they appear in the plan table."""
    skip_item = RenameItem(
        "/root/Anime/unknown.mkv",
        "(AniDB lookup failed)",
        kind=RenameKind.SKIP,
    )
    flat, conflicts = flatten_and_validate_folder_renames([([skip_item], "")])
    assert len(conflicts) == 0
    assert len(flat) == 1
    assert flat[0].kind == RenameKind.SKIP
    assert flat[0].old_path == "/root/Anime/unknown.mkv"
    assert flat[0].new_path == "(AniDB lookup failed)"


def test_flatten_and_validate_folder_renames_multiple_targets_conflict() -> None:
    """Flatten returns only file items; directory items ignored; no conflict message."""
    items1 = [
        RenameItem(
            "/root/Anime/ep01.mkv", "/root/ShowA [GroupA]/ShowA - 01.mkv", kind=RenameKind.FILE
        ),
        RenameItem("/root/Anime", "/root/ShowA [GroupA]", kind=RenameKind.DIRECTORY),
    ]
    items2 = [
        RenameItem(
            "/root/Anime/ep02.mkv", "/root/ShowB [GroupB]/ShowB - 02.mkv", kind=RenameKind.FILE
        ),
        RenameItem("/root/Anime", "/root/ShowB [GroupB]", kind=RenameKind.DIRECTORY),
    ]
    flat, conflicts = flatten_and_validate_folder_renames([(items1, ""), (items2, "")])
    assert len(conflicts) == 0
    file_items = [i for i in flat if i.kind == RenameKind.FILE]
    folder_items = [i for i in flat if i.kind == RenameKind.DIRECTORY]
    assert len(file_items) == 2
    assert len(folder_items) == 0
