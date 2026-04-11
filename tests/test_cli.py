"""CLI tests: --help, no paths, dry-run with no videos, interrupt logout, --plex."""

from __future__ import annotations

import sys
from pathlib import Path, WindowsPath
from unittest.mock import MagicMock, patch

import pytest

from anipyrenamer.cli import (
    EXIT_INTERRUPTED,
    _apply_plex_suffix,
    _apply_suffix_conflict_resolution,
    _get_well_known_env_path,
    _prompt_confirmation,
    main,
)
from anipyrenamer.models import DiscoveredGroup, FileInfo, RenameItem, RenameKind
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


def test_cli_keyboard_interrupt_calls_logout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
                RenameItem(
                    "/dir/ep01.mkv", "/parent/Show [Subs]/Show - 01.mkv", kind=RenameKind.FILE
                ),
            ],
            "/dir/ep01.mkv",
        ),
        (
            [
                RenameItem(
                    "/dir/ep02.mkv", "/parent/Show [Subs]/Show - 02.mkv", kind=RenameKind.FILE
                ),
            ],
            "/dir/ep02.mkv",
        ),
    ]
    flat, conflicts = flatten_and_validate_folder_renames(all_items)
    assert len(conflicts) == 0
    assert len(flat) == 2
    assert all(i.kind == RenameKind.FILE for i in flat)
    assert all("/parent/Show [Subs]/" in i.new_path for i in flat)


def test_all_p0_p1_flags_exist(tmp_path: Path) -> None:
    """Verify argparse has all P0/P1 flags from the spec priority table."""
    db = tmp_path / "test.sqlite"
    orig_argv = sys.argv
    try:
        sys.argv = [
            "anipyrenamer",
            str(tmp_path),
            "--dry-run",
            "--yes",
            "--template",
            "%title%%ext%",
            "--folder",
            "--folder-template",
            "%title%",
            "--plex",
            "--dest",
            str(tmp_path / "out"),
            "--db",
            str(db),
            "--offline",
            "--on-conflict",
            "suffix",
            "--name-dedupe",
            "hash",
            "--preview-format",
            "json",
            "--refresh-cache",
        ]
        with patch("anipyrenamer.cli.discover", return_value=[]):
            try:
                main()
            except SystemExit as e:
                assert e.code == 0, f"Expected exit 0, got {e.code}"
    finally:
        sys.argv = orig_argv


def test_cli_on_conflict_fail_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """--on-conflict=fail aborts when two planned items target the same destination."""
    info = FileInfo(
        fid=1,
        aid=2,
        eid=3,
        gid=4,
        size=10,
        ed2k="A" * 32,
        quality="high",
        source="TV",
        anime_title="Show",
        episode_number="01",
        episode_title="Pilot",
        group_name="Group",
    )
    groups = [
        DiscoveredGroup(video_path="/in/a.mkv", sidecar_paths=()),
        DiscoveredGroup(video_path="/in/b.mkv", sidecar_paths=()),
    ]
    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "/in", "--dry-run", "--on-conflict", "fail", "--offline"]
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", return_value="A" * 32),
            patch("anipyrenamer.cli.get_file_info", return_value=info),
            patch(
                "anipyrenamer.cli.build_plan",
                side_effect=[
                    [RenameItem("/in/a.mkv", "/dest/same.mkv", kind=RenameKind.FILE)],
                    [RenameItem("/in/b.mkv", "/dest/same.mkv", kind=RenameKind.FILE)],
                ],
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
    finally:
        sys.argv = orig_argv


def test_apply_suffix_conflict_resolution_dedupes_planned_collisions() -> None:
    """--on-conflict=suffix dedupes planned same-destination targets."""
    items = [
        RenameItem("/in/a.mkv", "/dest/same.mkv", kind=RenameKind.FILE),
        RenameItem("/in/b.mkv", "/dest/same.mkv", kind=RenameKind.FILE),
    ]
    _apply_suffix_conflict_resolution(items, strategy="counter")
    assert items[0].new_path == "/dest/same.mkv"
    assert items[1].new_path.endswith("same (2).mkv")
    assert items[0].new_path != items[1].new_path


def test_apply_suffix_conflict_resolution_dedupes_existing_destination(tmp_path: Path) -> None:
    """--on-conflict=suffix rewrites destination when target file already exists."""
    src = tmp_path / "src.mkv"
    src.write_bytes(b"src")
    existing = tmp_path / "existing.mkv"
    existing.write_bytes(b"existing")
    item = RenameItem(str(src), str(existing), kind=RenameKind.FILE)
    _apply_suffix_conflict_resolution([item], strategy="counter")
    assert item.new_path != str(existing)
    assert item.new_path.endswith("existing (2).mkv")


def test_prompt_confirmation_enter_defaults_to_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert _prompt_confirmation("Apply these renames?") == "y"


def test_prompt_confirmation_accepts_all_option(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "a")
    assert _prompt_confirmation("Apply these renames?") == "a"


def test_prompt_confirmation_reprompts_on_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter(["maybe", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))
    assert _prompt_confirmation("Apply these renames?") == "n"


def test_cli_mylist_invokes_wizard_on_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    info = FileInfo(
        fid=123,
        aid=2,
        eid=3,
        gid=4,
        size=10,
        ed2k="A" * 32,
        quality="high",
        source="TV",
        anime_title="Show",
        episode_number="01",
        episode_title="Pilot",
        group_name="Group",
    )
    groups = [DiscoveredGroup(video_path="/in/a.mkv", sidecar_paths=())]
    called = {"value": False}

    class _Result:
        attempted = False
        failed = 0

    def _fake_wizard(*, console, client, file_infos, confirm):  # noqa: ANN001, ARG001
        called["value"] = True
        assert len(file_infos) == 1
        return _Result()

    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "/in", "--dry-run", "--offline", "--mylist"]
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", return_value="A" * 32),
            patch("anipyrenamer.cli.get_file_info", return_value=info),
            patch(
                "anipyrenamer.cli.build_plan",
                return_value=[RenameItem("/in/a.mkv", "/dest/a.mkv", kind=RenameKind.FILE)],
            ),
            patch("anipyrenamer.cli.run_mylist_wizard", side_effect=_fake_wizard),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        assert called["value"] is True
    finally:
        sys.argv = orig_argv


def test_get_well_known_env_path_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows, well-known path is %APPDATA%\\anipyrenamer\\.env."""
    monkeypatch.setattr("os.name", "nt")
    monkeypatch.setenv("APPDATA", "C:\\Users\\Me\\AppData\\Roaming")
    assert _get_well_known_env_path() == Path("C:/Users/Me/AppData/Roaming/anipyrenamer/.env")


def test_get_well_known_env_path_windows_no_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows with APPDATA unset, well-known path is None."""
    monkeypatch.setattr("os.name", "nt")
    monkeypatch.delenv("APPDATA", raising=False)
    assert _get_well_known_env_path() is None


def test_get_well_known_env_path_unix(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Unix, well-known path is ~/.config/anipyrenamer/.env."""
    monkeypatch.setattr("os.name", "posix")
    # Keep WindowsPath for / operator on Windows test runner (pathlib uses PosixPath when os.name is posix)
    monkeypatch.setattr("anipyrenamer.cli.Path", WindowsPath)
    monkeypatch.setattr(WindowsPath, "home", lambda: WindowsPath("/home/user"))
    assert _get_well_known_env_path() == WindowsPath("/home/user/.config/anipyrenamer/.env")
