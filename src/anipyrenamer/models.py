"""DTOs for AniDB and rename pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FileInfo:
    """FILE command response: fid, aid, eid, gid, quality, source, etc."""

    fid: int
    aid: int
    eid: int
    gid: int
    size: int
    ed2k: str
    quality: str
    source: str
    group_name: str = ""
    anime_title: str = ""
    episode_number: str = ""
    episode_title: str = ""
    file_version: str = ""


@dataclass
class DiscoveredGroup:
    """One video file and its sidecar files (same stem)."""

    video_path: str
    sidecar_paths: tuple[str, ...]


@dataclass
class RenameItem:
    """Single rename: old_path -> new_path."""

    old_path: str
    new_path: str
