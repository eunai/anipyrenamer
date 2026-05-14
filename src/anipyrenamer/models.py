"""DTOs for AniDB and rename pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RenameKind(str, Enum):
    """Whether a rename item is a file, directory, or skipped (display-only; not applied)."""

    FILE = "file"
    DIRECTORY = "directory"
    SKIP = "skip"  # Discovered but AniDB lookup failed; shown in table, not renamed


@dataclass
class FileInfo:
    """FILE + ANIME + EP + GROUP data; all optional fields cached for template tokens."""

    fid: int
    aid: int
    eid: int
    gid: int
    size: int
    ed2k: str
    quality: str
    source: str
    group_name: str = ""
    group_short_name: str = ""
    anime_title: str = ""
    episode_number: str = ""
    episode_title: str = ""
    file_version: str = ""
    # Anime titles (AniAdd: ATr, ATe, ATk, ATs, ATo)
    title_romaji: str = ""
    title_english: str = ""
    title_kanji: str = ""
    title_synonym: str = ""
    title_other: str = ""
    # Episode titles (ETr, ETe, ETk)
    eptitle_romaji: str = ""
    eptitle_english: str = ""
    eptitle_kanji: str = ""
    # Anime info
    ep_count: str = ""
    ep_highest: str = ""
    year_begin: str = ""
    year_end: str = ""
    categories: str = ""
    anime_type: str = ""
    # File flags/info
    deprecated: str = ""
    censored: str = ""
    anidb_filename: str = ""
    crc: str = ""
    video_resolution: str = ""
    audio_codec: str = ""
    video_codec: str = ""
    audio_langs: str = ""
    subtitle_langs: str = ""
    duration: str = ""
    watched: str = ""


@dataclass
class DiscoveredGroup:
    """One video file and its sidecar files (same stem)."""

    video_path: str
    sidecar_paths: tuple[str, ...]


@dataclass
class RenameItem:
    """Single rename: old_path -> new_path. kind determines apply order (files first, then folders)."""

    old_path: str
    new_path: str
    kind: RenameKind = RenameKind.FILE
    anime_type: str = ""  # For preview Type column (tv, movie, ova, web, etc.); from %anime_type%
