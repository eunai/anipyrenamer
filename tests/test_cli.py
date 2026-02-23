"""CLI tests: --help, no paths, dry-run with no videos."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from anipyrenamer.cli import main


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
