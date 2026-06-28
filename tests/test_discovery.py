"""Tests for discovery (video + sidecars)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from anipyrenamer.discovery import _strip_trailing_windows_quote_artifact, discover


def test_discover_empty_paths() -> None:
    assert discover([]) == []


def test_discover_single_video_file(tmp_path: Path) -> None:
    (tmp_path / "a.mkv").write_bytes(b"x")
    groups = discover([str(tmp_path)])
    assert len(groups) == 1
    assert groups[0].video_path == str(tmp_path / "a.mkv")
    assert groups[0].sidecar_paths == ()


def test_discover_video_with_sidecars(tmp_path: Path) -> None:
    (tmp_path / "ep01.mkv").write_bytes(b"x")
    (tmp_path / "ep01.ass").write_bytes(b"[Script Info]")
    (tmp_path / "ep01.srt").write_bytes(b"1")
    groups = discover([str(tmp_path)])
    assert len(groups) == 1
    assert groups[0].video_path == str(tmp_path / "ep01.mkv")
    assert len(groups[0].sidecar_paths) == 2
    assert str(tmp_path / "ep01.ass") in groups[0].sidecar_paths
    assert str(tmp_path / "ep01.srt") in groups[0].sidecar_paths


def test_discover_ignores_non_video(tmp_path: Path) -> None:
    (tmp_path / "readme.txt").write_bytes(b"x")
    groups = discover([str(tmp_path)])
    assert len(groups) == 0


def test_discover_path_with_trailing_sep(tmp_path: Path) -> None:
    """Path with trailing slash/backslash still finds videos in directory."""
    (tmp_path / "a.mkv").write_bytes(b"x")
    path_with_trail = str(tmp_path) + ("\\" if __import__("sys").platform == "win32" else "/")
    groups = discover([path_with_trail])
    assert len(groups) == 1
    assert groups[0].video_path == str(tmp_path / "a.mkv")


def test_discover_directory_one_level_down(tmp_path: Path) -> None:
    """Scan finds videos in directory and one level down in subdirs."""
    (tmp_path / "top.mkv").write_bytes(b"x")
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "nested.mkv").write_bytes(b"x")
    groups = discover([str(tmp_path)])
    assert len(groups) == 2
    paths = {g.video_path for g in groups}
    assert str(tmp_path / "top.mkv") in paths
    assert str(sub / "nested.mkv") in paths


def test_discover_directory_two_levels_down(tmp_path: Path) -> None:
    """Scan finds videos two levels down (e.g. Show/Season X/episode.mkv)."""
    (tmp_path / "top.mkv").write_bytes(b"x")
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "nested.mkv").write_bytes(b"x")
    sub2 = sub / "Season 3"
    sub2.mkdir()
    (sub2 / "ep07.mkv").write_bytes(b"x")
    groups = discover([str(tmp_path)])
    assert len(groups) == 3
    paths = {g.video_path for g in groups}
    assert str(tmp_path / "top.mkv") in paths
    assert str(sub / "nested.mkv") in paths
    assert str(sub2 / "ep07.mkv") in paths


# --- _strip_trailing_windows_quote_artifact: pure rule, tested cross-platform ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('C:\\dir"', "C:\\dir"),  # PowerShell swallowed-slash form
        ('C:\\dir\\"', "C:\\dir"),  # backslash-preserved-then-quote form
        ('C:\\dir"""', "C:\\dir"),  # multiple trailing quotes
        ('C:\\di"r"', 'C:\\di"r'),  # internal quote kept; only the trailing one stripped
        ("C:\\dir\\", "C:\\dir"),  # existing trailing-separator trim preserved
    ],
)
def test_strip_quote_artifact_windows(raw: str, expected: str) -> None:
    assert _strip_trailing_windows_quote_artifact(raw, is_windows=True) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('/tmp/dir"', '/tmp/dir"'),  # POSIX: '"' is a legal filename char; not stripped
        ("/tmp/dir/", "/tmp/dir"),  # POSIX: trailing separator still trimmed
    ],
)
def test_strip_quote_artifact_posix(raw: str, expected: str) -> None:
    assert _strip_trailing_windows_quote_artifact(raw, is_windows=False) == expected


@pytest.mark.skipif(
    os.name != "nt", reason="Windows-only PowerShell trailing-backslash path-arg quirk"
)
def test_discover_strips_windows_trailing_quote_artifact(tmp_path: Path) -> None:
    """Regression for #23: a Windows directory arg arriving with the stray trailing quote
    (PowerShell turns a quoted arg ending in a backslash into one ending in '"') still
    discovers its videos instead of reporting none."""
    (tmp_path / "ep01.mkv").write_bytes(b"x")
    mangled = str(tmp_path) + '"'
    groups = discover([mangled])
    assert len(groups) == 1
    assert groups[0].video_path == str(tmp_path / "ep01.mkv")
