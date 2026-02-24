"""Tests for rename plan building."""
from __future__ import annotations

from pathlib import Path

import pytest

from anipyrenamer.models import DiscoveredGroup, FileInfo, RenameItem
from anipyrenamer.plan import build_plan


def test_build_plan_in_place() -> None:
    group = DiscoveredGroup(
        video_path="/dir/Show - 01.mkv",
        sidecar_paths=("/dir/Show - 01.ass",),
    )
    info = FileInfo(
        fid=1,
        aid=2,
        eid=3,
        gid=4,
        size=100,
        ed2k="x" * 32,
        quality="high",
        source="TV",
        anime_title="My Show",
        episode_number="01",
        episode_title="Pilot",
        group_name="Subs",
    )
    items = build_plan(group, info, "%title% - %epno% - %eptitle% [%group%]%ext%", dest_root=None)
    assert len(items) == 2
    assert items[0].old_path == "/dir/Show - 01.mkv"
    assert items[0].new_path.endswith(".mkv")
    # Title sanitized to no spaces; epno and content present
    assert "01" in items[0].new_path and ("My-Show" in items[0].new_path or "My Show" in items[0].new_path)
    assert items[1].old_path == "/dir/Show - 01.ass"
    assert items[1].new_path.endswith(".ass")


def test_build_plan_with_dest() -> None:
    group = DiscoveredGroup(video_path="/a/v.mkv", sidecar_paths=())
    info = FileInfo(
        1, 2, 3, 4, 100, "e" * 32, "high", "TV",
        anime_title="T", episode_number="1",
    )
    # Use short template to avoid needing all fields
    items = build_plan(group, info, "%title% - %epno%%ext%", dest_root="/dest")
    assert len(items) == 1
    assert "dest" in items[0].new_path  # path may use / or \
    assert items[0].new_path.endswith(".mkv")


def test_build_plan_with_folder_template_in_place() -> None:
    """With folder_template set and in-place, plan includes one folder RenameItem and file items."""
    group = DiscoveredGroup(
        video_path="/root/MyDir/ep01.mkv",
        sidecar_paths=(),
    )
    info = FileInfo(
        fid=1,
        aid=2,
        eid=3,
        gid=4,
        size=100,
        ed2k="x" * 32,
        quality="high",
        source="TV",
        anime_title="My Show",
        episode_number="01",
        episode_title="Pilot",
        group_name="Subs",
    )
    items = build_plan(
        group,
        info,
        "%title% - %epno%%ext%",
        dest_root=None,
        folder_template="%title% [%group%]%ext%",
    )
    # One video item + one folder item
    assert len(items) >= 2
    parent_dir = str(Path(group.video_path).parent)
    file_items = [i for i in items if i.old_path.endswith(".mkv")]
    folder_items = [i for i in items if i.old_path == parent_dir]
    assert len(file_items) == 1
    assert len(folder_items) == 1
    assert "My Show" in folder_items[0].new_path or "My-Show" in folder_items[0].new_path
    assert "Subs" in folder_items[0].new_path


def test_build_plan_folder_template_with_dest_omits_folder_rename() -> None:
    """With dest_root set, folder_template is ignored (no folder item)."""
    group = DiscoveredGroup(video_path="/a/v.mkv", sidecar_paths=())
    info = FileInfo(
        1, 2, 3, 4, 100, "e" * 32, "high", "TV",
        anime_title="T", episode_number="1", group_name="G",
    )
    items = build_plan(
        group, info, "%title% - %epno%%ext%",
        dest_root="/dest",
        folder_template="%title% [%group%]%ext%",
    )
    assert len(items) == 1
    assert "dest" in items[0].new_path


def test_build_plan_two_groups_same_dir_same_folder_target() -> None:
    """Two episodes in the same folder produce same folder old_path (CLI deduplicates)."""
    info = FileInfo(
        fid=1, aid=2, eid=3, gid=4, size=100, ed2k="x" * 32,
        quality="high", source="TV",
        anime_title="Show", episode_number="01", episode_title="Ep1", group_name="Subs",
    )
    group1 = DiscoveredGroup(video_path="/root/AnimeDir/ep01.mkv", sidecar_paths=())
    group2 = DiscoveredGroup(video_path="/root/AnimeDir/ep02.mkv", sidecar_paths=())
    items1 = build_plan(group1, info, "%title% - %epno%%ext%", folder_template="%title% [%group%]%ext%")
    items2 = build_plan(group2, info, "%title% - %epno%%ext%", folder_template="%title% [%group%]%ext%")
    parent_dir = str(Path(group1.video_path).parent)
    folder1 = [i for i in items1 if i.old_path == parent_dir][0]
    folder2 = [i for i in items2 if i.old_path == parent_dir][0]
    assert folder1.old_path == folder2.old_path
    assert folder1.new_path == folder2.new_path
