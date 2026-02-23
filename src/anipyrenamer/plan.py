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
    current_filename = video_path.name
    base_name = render_template(
        template,
        title=info.anime_title,
        epno=info.episode_number,
        eptitle=info.episode_title,
        group=info.group_short_name or info.group_name,
        group_long=info.group_name,
        extension=ext,
        fileversion=info.file_version,
        aid=str(info.aid),
        eid=str(info.eid),
        fid=str(info.fid),
        gid=str(info.gid),
        title_romaji=info.title_romaji,
        title_english=info.title_english,
        title_kanji=info.title_kanji,
        title_synonym=info.title_synonym,
        title_other=info.title_other,
        eptitle_romaji=info.eptitle_romaji,
        eptitle_english=info.eptitle_english,
        eptitle_kanji=info.eptitle_kanji,
        ep_count=info.ep_count,
        ep_highest=info.ep_highest,
        year_begin=info.year_begin,
        year_end=info.year_end,
        categories=info.categories,
        anime_type=info.anime_type,
        deprecated=info.deprecated,
        censored=info.censored,
        source=info.source,
        quality=info.quality,
        anidb_filename=info.anidb_filename,
        current_filename=current_filename,
        crc=info.crc,
        video_resolution=info.video_resolution,
        audio_codec=info.audio_codec,
        video_codec=info.video_codec,
        audio_langs=info.audio_langs,
        subtitle_langs=info.subtitle_langs,
        duration=info.duration,
        watched=info.watched,
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
