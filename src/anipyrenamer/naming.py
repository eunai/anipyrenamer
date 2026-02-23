"""Naming template and filename sanitization. Tokens match AniAdd-style (readable names)."""

from __future__ import annotations

import re

# Characters illegal in Windows filenames; also sanitize for cross-platform.
ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|\s]+')


def _sanitize(s: str) -> str:
    """Safe filename: no \\ / : * ? \" < > |; collapse whitespace; trim trailing dashes."""
    out = ILLEGAL_CHARS.sub("-", s.strip())
    out = re.sub(r"-+", "-", out).strip("-").strip()
    return out or "Unknown"


def render_template(
    template: str,
    *,
    title: str = "",
    epno: str = "",
    eptitle: str = "",
    group: str = "",
    group_long: str = "",
    extension: str = "",
    fileversion: str = "",
    aid: str = "",
    eid: str = "",
    fid: str = "",
    gid: str = "",
    # AniAdd-style (readable tokens)
    title_romaji: str = "",
    title_english: str = "",
    title_kanji: str = "",
    title_synonym: str = "",
    title_other: str = "",
    eptitle_romaji: str = "",
    eptitle_english: str = "",
    eptitle_kanji: str = "",
    ep_count: str = "",
    ep_highest: str = "",
    year_begin: str = "",
    year_end: str = "",
    categories: str = "",
    anime_type: str = "",
    deprecated: str = "",
    censored: str = "",
    source: str = "",
    quality: str = "",
    anidb_filename: str = "",
    current_filename: str = "",
    crc: str = "",
    video_resolution: str = "",
    audio_codec: str = "",
    video_codec: str = "",
    audio_langs: str = "",
    subtitle_langs: str = "",
    duration: str = "",
    watched: str = "",
) -> str:
    """Replace tokens; result sanitized for filenames. See DEFAULT_TEMPLATE and docs."""
    out = template
    out = out.replace("%title%", _sanitize(title) or _sanitize(title_english) or _sanitize(title_romaji) or "Unknown")
    out = out.replace("%title_romaji%", _sanitize(title_romaji) or "")
    out = out.replace("%title_english%", _sanitize(title_english) or "")
    out = out.replace("%title_kanji%", _sanitize(title_kanji) or "")
    out = out.replace("%title_synonym%", _sanitize(title_synonym) or "")
    out = out.replace("%title_other%", _sanitize(title_other) or "")
    out = out.replace("%epno%", epno or "0")
    out = out.replace("%eptitle%", _sanitize(eptitle) or _sanitize(eptitle_english) or "Episode")
    out = out.replace("%eptitle_romaji%", _sanitize(eptitle_romaji) or "")
    out = out.replace("%eptitle_english%", _sanitize(eptitle_english) or "")
    out = out.replace("%eptitle_kanji%", _sanitize(eptitle_kanji) or "")
    out = out.replace("%fileversion%", fileversion or "")
    out = out.replace("%group%", _sanitize(group) or _sanitize(group_long) or "Unknown")
    out = out.replace("%grouplong%", _sanitize(group_long) or _sanitize(group) or "Unknown")
    out = out.replace("%ext%", extension or "")
    out = out.replace("%aid%", aid or "0")
    out = out.replace("%eid%", eid or "0")
    out = out.replace("%fid%", fid or "0")
    out = out.replace("%gid%", gid or "0")
    out = out.replace("%ep_count%", ep_count or "")
    out = out.replace("%ep_highest%", ep_highest or "")
    out = out.replace("%year_begin%", year_begin or "")
    out = out.replace("%year_end%", year_end or "")
    out = out.replace("%categories%", _sanitize(categories) or "")
    out = out.replace("%anime_type%", _sanitize(anime_type) or "")
    out = out.replace("%deprecated%", deprecated or "")
    out = out.replace("%censored%", censored or "")
    out = out.replace("%source%", _sanitize(source) or "")
    out = out.replace("%quality%", _sanitize(quality) or "")
    out = out.replace("%anidb_filename%", _sanitize(anidb_filename) or "")
    out = out.replace("%current_filename%", _sanitize(current_filename) or "")
    out = out.replace("%crc%", crc or "")
    out = out.replace("%video_resolution%", video_resolution or "")
    out = out.replace("%audio_codec%", _sanitize(audio_codec) or "")
    out = out.replace("%video_codec%", _sanitize(video_codec) or "")
    out = out.replace("%audio_langs%", _sanitize(audio_langs) or "")
    out = out.replace("%subtitle_langs%", _sanitize(subtitle_langs) or "")
    out = out.replace("%duration%", duration or "")
    out = out.replace("%watched%", watched or "")
    return _sanitize(out) or "Unknown"


DEFAULT_TEMPLATE = "%title% - %epno%%fileversion% - %eptitle% [%group%]%ext%"
