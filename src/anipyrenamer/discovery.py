"""Discover video files and sidecars (same stem) from paths."""

from __future__ import annotations

import os
from pathlib import Path

from anipyrenamer.models import DiscoveredGroup

# One-level scan: only direct children of directories.
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".wmv", ".flv"}
SIDECAR_EXTENSIONS = {".ass", ".srt", ".ssa", ".sub", ".idx", ".nfo", ".sup"}


def discover(paths: list[str]) -> list[DiscoveredGroup]:
    """
    Scan paths for video files and their sidecars (same stem).
    Paths can be files or directories; directories are scanned one level only.
    """
    seen_stems: set[tuple[Path, str]] = set()
    groups: list[DiscoveredGroup] = []
    for raw in paths:
        p = Path(raw).resolve()
        if not p.exists():
            continue
        if p.is_file():
            _add_file(p, Path(p.parent), seen_stems, groups)
        else:
            for child in p.iterdir():
                if child.is_file():
                    _add_file(child, p, seen_stems, groups)
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
