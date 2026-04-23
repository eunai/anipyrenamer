"""Tests for rename plan building."""

from __future__ import annotations

from pathlib import Path

import pytest

from anipyrenamer.models import DiscoveredGroup, FileInfo, RenameKind
from anipyrenamer.plan import _validate_path_containment, build_plan


def test_validate_path_containment_rejects_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "expected"
    root.mkdir()
    outside = tmp_path / "other" / "f.mkv"
    outside.parent.mkdir()
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="escapes destination root"):
        _validate_path_containment(str(outside), root)


def test_validate_path_containment_rejects_dotdot(tmp_path: Path) -> None:
    """SEC-05 / P3-A: bare '..' segment escapes the root."""
    root = tmp_path / "anime"
    root.mkdir()
    with pytest.raises(ValueError, match="escapes destination root"):
        _validate_path_containment(str(root / ".." / "foo"), root)


def test_validate_path_containment_accepts_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "expected"
    root.mkdir()
    inner = root / "sub" / "a.mkv"
    inner.parent.mkdir(parents=True)
    inner.write_text("x", encoding="utf-8")
    _validate_path_containment(str(inner), root)


def test_build_plan_rejects_template_that_escapes_dest_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-05: resolved output must stay under dest_root when set."""
    dest = tmp_path / "dest"
    dest.mkdir()
    sub = tmp_path / "src" / "sub"
    sub.mkdir(parents=True)
    video = sub / "v.mkv"
    video.write_text("x", encoding="utf-8")
    group = DiscoveredGroup(video_path=str(video), sidecar_paths=())
    info = FileInfo(
        fid=1,
        aid=2,
        eid=3,
        gid=4,
        size=100,
        ed2k="e" * 32,
        quality="high",
        source="TV",
        anime_title="T",
        episode_number="1",
    )
    monkeypatch.setattr("anipyrenamer.plan.render_template", lambda *_a, **_k: "..")
    with pytest.raises(ValueError, match="escapes destination root"):
        build_plan(group, info, "%title%%ext%", dest_root=str(dest))


def test_build_plan_folder_template_stays_under_grandparent(tmp_path: Path) -> None:
    """SEC-05: in-place --folder mode — expected root is video_path.parent.parent."""
    root = tmp_path / "root"
    show_dir = root / "MyDir"
    show_dir.mkdir(parents=True)
    video = show_dir / "ep01.mkv"
    video.write_text("x", encoding="utf-8")
    group = DiscoveredGroup(video_path=str(video), sidecar_paths=())
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
    new_path = Path(items[0].new_path)
    assert new_path.is_relative_to(root)
    assert "Subs" in new_path.parent.name


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
    assert all(i.kind == RenameKind.FILE for i in items)
    assert items[0].old_path == "/dir/Show - 01.mkv"
    assert items[0].new_path.endswith(".mkv")
    # Title sanitized to no spaces; epno and content present
    assert "01" in items[0].new_path and (
        "My-Show" in items[0].new_path or "My Show" in items[0].new_path
    )
    assert items[1].old_path == "/dir/Show - 01.ass"
    assert items[1].new_path.endswith(".ass")


def test_build_plan_with_dest() -> None:
    group = DiscoveredGroup(video_path="/a/v.mkv", sidecar_paths=())
    info = FileInfo(
        1,
        2,
        3,
        4,
        100,
        "e" * 32,
        "high",
        "TV",
        anime_title="T",
        episode_number="1",
    )
    # Use short template to avoid needing all fields
    items = build_plan(group, info, "%title% - %epno%%ext%", dest_root="/dest")
    assert len(items) == 1
    assert "dest" in items[0].new_path  # path may use / or \
    assert items[0].new_path.endswith(".mkv")


def test_build_plan_with_folder_template_in_place() -> None:
    """With folder_template set and in-place, plan has file items under target folder; no directory item."""
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
    assert len(items) == 1
    file_items = [i for i in items if i.kind == RenameKind.FILE]
    folder_items = [i for i in items if i.kind == RenameKind.DIRECTORY]
    assert len(file_items) == 1
    assert len(folder_items) == 0
    # File new_path is under target folder (parent.parent / folder_name)
    new_path = Path(file_items[0].new_path)
    assert new_path.parent.name != "MyDir"  # not in original dir
    assert "Subs" in new_path.parent.name
    assert file_items[0].old_path == "/root/MyDir/ep01.mkv"


def test_build_plan_folder_template_with_dest_omits_folder_rename() -> None:
    """With dest_root set, folder_template is ignored (no folder item)."""
    group = DiscoveredGroup(video_path="/a/v.mkv", sidecar_paths=())
    info = FileInfo(
        1,
        2,
        3,
        4,
        100,
        "e" * 32,
        "high",
        "TV",
        anime_title="T",
        episode_number="1",
        group_name="G",
    )
    items = build_plan(
        group,
        info,
        "%title% - %epno%%ext%",
        dest_root="/dest",
        folder_template="%title% [%group%]%ext%",
    )
    assert len(items) == 1
    assert "dest" in items[0].new_path


def test_build_plan_with_plex_folder_template() -> None:
    """Plex-modified folder template produces folder name with [anidb-<aid>]."""
    group = DiscoveredGroup(
        video_path="/root/MyDir/ep01.mkv",
        sidecar_paths=(),
    )
    info = FileInfo(
        fid=1,
        aid=9876,
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
    plex_folder_tpl = "%title% [%group%] [anidb-%aid%]%ext%"
    items = build_plan(
        group,
        info,
        "%title% - %epno%%ext%",
        dest_root=None,
        folder_template=plex_folder_tpl,
    )
    assert len(items) == 1
    new_path = Path(items[0].new_path)
    assert "[anidb-9876]" in new_path.parent.name
    assert "Subs" in new_path.parent.name


def test_build_plan_two_groups_same_dir_same_folder_target() -> None:
    """Two episodes in the same folder produce file items with the same target parent (no directory items)."""
    info = FileInfo(
        fid=1,
        aid=2,
        eid=3,
        gid=4,
        size=100,
        ed2k="x" * 32,
        quality="high",
        source="TV",
        anime_title="Show",
        episode_number="01",
        episode_title="Ep1",
        group_name="Subs",
    )
    group1 = DiscoveredGroup(video_path="/root/AnimeDir/ep01.mkv", sidecar_paths=())
    group2 = DiscoveredGroup(video_path="/root/AnimeDir/ep02.mkv", sidecar_paths=())
    items1 = build_plan(
        group1, info, "%title% - %epno%%ext%", folder_template="%title% [%group%]%ext%"
    )
    items2 = build_plan(
        group2, info, "%title% - %epno%%ext%", folder_template="%title% [%group%]%ext%"
    )
    assert len(items1) == 1 and len(items2) == 1
    assert items1[0].kind == RenameKind.FILE and items2[0].kind == RenameKind.FILE
    assert Path(items1[0].new_path).parent == Path(items2[0].new_path).parent
