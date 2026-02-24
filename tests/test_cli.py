"""CLI tests: --help, no paths, dry-run with no videos, interrupt logout, --plex."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anipyrenamer.cli import EXIT_INTERRUPTED, PLEX_SUFFIX, _apply_plex_suffix, main
from anipyrenamer.models import RenameItem, RenameKind
from anipyrenamer.naming import DEFAULT_FOLDER_TEMPLATE
from anipyrenamer.validation import flatten_and_validate_folder_renames


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


def test_cli_keyboard_interrupt_calls_logout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On KeyboardInterrupt during hashing/lookup, AniDB client logout is called and exit is 130."""
    (tmp_path / "a.mkv").write_bytes(b"x")
    monkeypatch.setenv("ANIDB_USERNAME", "u")
    monkeypatch.setenv("ANIDB_PASSWORD", "p")
    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", str(tmp_path)]
        with patch("anipyrenamer.anidb.AniDBClient") as MockAniDBClient:
            mock_client = MagicMock()
            MockAniDBClient.return_value = mock_client
            mock_client.login.return_value = (True, "")
            mock_client._session = "fake"
            with patch("anipyrenamer.cli.compute_ed2k", side_effect=KeyboardInterrupt):
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == EXIT_INTERRUPTED
            mock_client.logout.assert_called()
    finally:
        sys.argv = orig_argv


def test_apply_plex_suffix_default_template() -> None:
    """Plex suffix is inserted before %ext% in the default folder template."""
    result = _apply_plex_suffix(DEFAULT_FOLDER_TEMPLATE)
    assert "[anidb-%aid%]" in result
    assert result.endswith("%ext%")
    assert result.index("[anidb-%aid%]") < result.index("%ext%")


def test_apply_plex_suffix_template_with_ext() -> None:
    """Plex suffix is inserted before %ext% when template contains it."""
    result = _apply_plex_suffix("%title% [%group%]%ext%")
    assert result == "%title% [%group%] [anidb-%aid%]%ext%"


def test_apply_plex_suffix_template_without_ext() -> None:
    """Plex suffix is appended when template has no %ext%."""
    result = _apply_plex_suffix("%title% [%group%]")
    assert result == "%title% [%group%] [anidb-%aid%]"


def test_apply_plex_suffix_custom_template() -> None:
    """Plex suffix works with a custom folder template."""
    result = _apply_plex_suffix("%title%%ext%")
    assert result == "%title% [anidb-%aid%]%ext%"


def test_flatten_validate_one_folder_per_dir_when_targets_agree() -> None:
    """Flatten returns only file items (new_path under target folder); no folder rename; no conflict."""
    all_items: list[tuple[list[RenameItem], str]] = [
        (
            [
                RenameItem("/dir/ep01.mkv", "/parent/Show [Subs]/Show - 01.mkv", kind=RenameKind.FILE),
            ],
            "/dir/ep01.mkv",
        ),
        (
            [
                RenameItem("/dir/ep02.mkv", "/parent/Show [Subs]/Show - 02.mkv", kind=RenameKind.FILE),
            ],
            "/dir/ep02.mkv",
        ),
    ]
    flat, conflicts = flatten_and_validate_folder_renames(all_items)
    assert len(conflicts) == 0
    assert len(flat) == 2
    assert all(i.kind == RenameKind.FILE for i in flat)
    assert all("/parent/Show [Subs]/" in i.new_path for i in flat)
