"""CLI tests: --help, no paths, dry-run with no videos."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from anipyrenamer.cli import _flatten_and_dedupe_renames, main
from anipyrenamer.models import RenameItem


def test_cli_help_exits_zero() -> None:
    """Running anipyrenamer --help should exit with code 0."""
    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "--help"]
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
    finally:
        sys.argv = orig_argv


def test_cli_no_paths_exits_zero() -> None:
    """Running anipyrenamer with no paths prints help and exits 0."""
    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer"]
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
    finally:
        sys.argv = orig_argv


def test_cli_dry_run_empty_dir(tmp_path: Path) -> None:
    """Running with --dry-run on dir with no videos exits 0 (no renames)."""
    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", str(tmp_path), "--dry-run"]
        main()
        # No SystemExit: main() runs to end and exits 0
    except SystemExit as e:
        assert e.code == 0
    finally:
        sys.argv = orig_argv


def test_flatten_dedupe_renames_one_folder_per_dir() -> None:
    """Multiple episodes in the same folder: flattened list has one folder rename per unique dir."""
    # Group 1: file + folder; Group 2: file + folder (same folder)
    all_items: list[tuple[list[RenameItem], str]] = [
        (
            [
                RenameItem("/dir/ep01.mkv", "/dir/Show - 01.mkv"),
                RenameItem("/dir", "/parent/Show [Subs]"),
            ],
            "/dir/ep01.mkv",
        ),
        (
            [
                RenameItem("/dir/ep02.mkv", "/dir/Show - 02.mkv"),
                RenameItem("/dir", "/parent/Show [Subs]"),
            ],
            "/dir/ep02.mkv",
        ),
    ]
    flat = _flatten_and_dedupe_renames(all_items)
    folder_renames = [i for i in flat if i.old_path == "/dir" and i.new_path == "/parent/Show [Subs]"]
    assert len(folder_renames) == 1
    assert len(flat) == 3  # 2 file renames + 1 folder rename
