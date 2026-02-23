"""Tests for discovery (video + sidecars)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from anipyrenamer.discovery import discover


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


def test_discover_directory_one_level_only(tmp_path: Path) -> None:
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "nested.mkv").write_bytes(b"x")
    groups = discover([str(tmp_path)])
    assert len(groups) == 0
    groups = discover([str(sub)])
    assert len(groups) == 1
