"""Discover video files and sidecars (same stem) from paths."""

from __future__ import annotations

import os
from pathlib import Path

from anipyrenamer.models import DiscoveredGroup

# Scan: direct children and up to two levels down (e.g. Show/Season X/episode.mkv).
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".wmv", ".flv"}
SIDECAR_EXTENSIONS = {".ass", ".srt", ".ssa", ".sub", ".idx", ".nfo", ".sup"}


def _strip_trailing_windows_quote_artifact(raw: str, *, is_windows: bool) -> str:
    """Trim a CLI path argument's trailing characters.

    Always strips surrounding whitespace and trailing path separators. On Windows,
    also strips stray trailing double-quote characters: PowerShell/cmd turn a quoted
    argument ending in a backslash into one ending in a double-quote (the backslash
    escapes the closing quote), and the double-quote is illegal in a Windows path, so
    a trailing one is always a shell artifact. POSIX is unchanged (a double-quote is a
    legal POSIX filename character).
    """
    cleaned = raw.strip()
    if is_windows:
        cleaned = cleaned.rstrip('"')
    cleaned = cleaned.rstrip("/\\")
    if is_windows:
        cleaned = cleaned.rstrip('"').rstrip("/\\")
    return cleaned


def _normalize_path(raw: str) -> Path:
    """Resolve a path arg; trim trailing separators (and, on Windows, a stray shell quote)."""
    cleaned = _strip_trailing_windows_quote_artifact(raw, is_windows=(os.name == "nt"))
    return Path(cleaned).resolve()


def discover(paths: list[str]) -> list[DiscoveredGroup]:
    """
    Scan paths for video files and their sidecars (same stem).
    Paths can be files or directories. Directories are scanned for direct children,
    one level down (each immediate subdirectory), and two levels down
    (e.g. Show/Season X/episode.mkv).
    """
    seen_stems: set[tuple[Path, str]] = set()
    groups: list[DiscoveredGroup] = []
    for raw in paths:
        try:
            p = _normalize_path(raw)
        except (OSError, RuntimeError):
            continue
        if not p.exists():
            continue
        if p.is_file():
            _add_file(p, Path(p.parent), seen_stems, groups)
        else:
            # Direct children (files in this directory)
            for child in p.iterdir():
                if child.is_file():
                    _add_file(child, p, seen_stems, groups)
            # One level down: files in each immediate subdirectory
            for child in p.iterdir():
                if child.is_dir():
                    for grandchild in child.iterdir():
                        if grandchild.is_file():
                            _add_file(grandchild, child, seen_stems, groups)
                    # Two levels down: e.g. Show/Season 3/episode.mkv
                    for grandchild in child.iterdir():
                        if grandchild.is_dir():
                            for great_grandchild in grandchild.iterdir():
                                if great_grandchild.is_file():
                                    _add_file(
                                        great_grandchild,
                                        grandchild,
                                        seen_stems,
                                        groups,
                                    )
    return groups


def _add_file(
    file_path: Path,
    scan_root: Path,
    seen_stems: set[tuple[Path, str]],
    groups: list[DiscoveredGroup],
) -> None:
    stem = file_path.stem
    key = (scan_root, stem)
    if key in seen_stems:
        return
    ext = file_path.suffix.lower()
    if ext not in VIDEO_EXTENSIONS:
        return
    seen_stems.add(key)
    parent = file_path.parent
    sidecars: list[str] = []
    for s in parent.iterdir():
        if not s.is_file() or s == file_path:
            continue
        if s.stem == stem and s.suffix.lower() in SIDECAR_EXTENSIONS:
            sidecars.append(str(s))
    groups.append(DiscoveredGroup(video_path=str(file_path), sidecar_paths=tuple(sorted(sidecars))))


def get_file_size(path: str) -> int:
    """Return file size in bytes."""
    return os.path.getsize(path)
