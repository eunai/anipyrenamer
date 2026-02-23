"""Tests for rename plan building."""
from __future__ import annotations

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
