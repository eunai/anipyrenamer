"""Build rename plan from discovered groups, FileInfo, and template."""

from __future__ import annotations

from pathlib import Path

from anipyrenamer.models import DiscoveredGroup, FileInfo, RenameItem
from anipyrenamer.naming import render_template


def build_plan(
    group: DiscoveredGroup,
    info: FileInfo,
    template: str,
    dest_root: str | None = None,
) -> list[RenameItem]:
    """
    Build (old_path, new_path) for the video and its sidecars.
    If dest_root is None, new paths are in-place (same dir as video).
    """
    video_path = Path(group.video_path)
    ext = video_path.suffix or ""
    base_name = render_template(
        template,
        title=info.anime_title,
        epno=info.episode_number,
        eptitle=info.episode_title,
        group=info.group_name,
        extension=ext,
        fileversion=info.file_version,
        aid=str(info.aid),
        eid=str(info.eid),
        fid=str(info.fid),
        gid=str(info.gid),
    )
    parent = video_path.parent
    if dest_root:
        parent = Path(dest_root)
    new_video = parent / f"{base_name}"
    items: list[RenameItem] = [RenameItem(old_path=group.video_path, new_path=str(new_video))]
    for old_side in group.sidecar_paths:
        p = Path(old_side)
        new_side = parent / f"{base_name.removesuffix(ext)}{p.suffix}"
        items.append(RenameItem(old_path=old_side, new_path=str(new_side)))
    return items
