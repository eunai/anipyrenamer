"""CLI tests: --help, no paths, dry-run with no videos, interrupt logout, --plex."""

from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path, WindowsPath
from unittest.mock import MagicMock, patch

import pytest

import anipyrenamer.cli as cli
from anipyrenamer.cli import (
    EXIT_INTERRUPTED,
    _apply_plex_suffix,
    _get_well_known_env_path,
    _hash_group,
    _prompt_confirmation,
    main,
)
from anipyrenamer.models import DiscoveredGroup, FileInfo, RenameItem, RenameKind
from anipyrenamer.naming import DEFAULT_FOLDER_TEMPLATE
from anipyrenamer.validation import flatten_and_validate_folder_renames


def test_cli_import_does_not_call_load_dotenv_until_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-10: importing cli does not load .env; main() invokes _load_env()."""
    calls: list[object] = []

    def fake_load_dotenv(*args: object, **kwargs: object) -> bool:
        calls.append((args, kwargs))
        return True

    import anipyrenamer.cli as cli_module

    importlib.reload(cli_module)
    monkeypatch.setattr(cli_module, "load_dotenv", fake_load_dotenv)
    assert calls == []

    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "--help"]
        with pytest.raises(SystemExit) as exc_info:
            cli_module.main()
        assert exc_info.value.code == 0
    finally:
        sys.argv = orig_argv

    assert len(calls) >= 1


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


def test_cli_structured_log_file_writes_phases(tmp_path: Path) -> None:
    """--log-file with --log-level INFO records key=value phases under anipyrenamer.* (UTF-8)."""
    log_path = tmp_path / "logs" / "run.log"
    orig_argv = sys.argv
    try:
        sys.argv = [
            "anipyrenamer",
            str(tmp_path),
            "--dry-run",
            "--offline",
            "--log-level",
            "INFO",
            "--log-file",
            str(log_path),
        ]
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
        groups = [DiscoveredGroup(video_path=str(tmp_path / "a.mkv"), sidecar_paths=())]
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", return_value="A" * 32),
            patch("anipyrenamer.cache.get_file_info", return_value=info),
            patch(
                "anipyrenamer.cli.build_plan",
                return_value=[
                    RenameItem(str(tmp_path / "a.mkv"), str(tmp_path / "out.mkv"), kind=RenameKind.FILE)
                ],
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
    finally:
        sys.argv = orig_argv
    lines = log_path.read_text(encoding="utf-8")
    assert "phase=discovery group_count=" in lines.replace("\n", " ")
    assert "phase=hash_lookup group_count=" in lines.replace("\n", " ")
    assert "phase=plan " in lines
    assert "phase=apply dry_run=yes" in lines.replace("\n", " ")


def test_cli_keyboard_interrupt_calls_logout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On KeyboardInterrupt during hashing/lookup, AniDB client logout is called and exit is 130."""
    (tmp_path / "a.mkv").write_bytes(b"x")
    monkeypatch.setenv("ANIDB_USERNAME", "u")
    monkeypatch.setenv("ANIDB_PASSWORD", "p")
    monkeypatch.delenv("ANIDB_API_KEY", raising=False)
    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", str(tmp_path)]
        with patch("anipyrenamer.anidb.AniDBClient") as MockAniDBClient:
            mock_client = MagicMock()
            MockAniDBClient.return_value = mock_client
            mock_client.encrypt.return_value = (True, "")
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
            "--log-level",
            "INFO",
            "--log-file",
            str(tmp_path / "cli_flags.log"),
        ]
        with patch("anipyrenamer.cli.discover", return_value=[]):
            try:
                main()
            except SystemExit as e:
                assert e.code == 0, f"Expected exit 0, got {e.code}"
    finally:
        sys.argv = orig_argv


def test_cli_batch_size_is_not_supported() -> None:
    """--batch-size is not part of the supported CLI surface."""
    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "--batch-size", "30"]
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2
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
            patch("anipyrenamer.cache.get_file_info", return_value=info),
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


def test_cli_dry_run_with_lookup_skip_exits_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mixed successful plan plus lookup skip is partial completion (exit 2)."""
    info = FileInfo(
        fid=1,
        aid=2,
        eid=3,
        gid=4,
        size=10,
        ed2k="B" * 32,
        quality="high",
        source="TV",
        anime_title="Show",
        episode_number="02",
        episode_title="Second",
        group_name="Group",
    )
    groups = [
        DiscoveredGroup(video_path="/in/a.mkv", sidecar_paths=()),
        DiscoveredGroup(video_path="/in/b.mkv", sidecar_paths=()),
    ]
    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "/in", "--dry-run", "--offline"]
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", side_effect=["A" * 32, "B" * 32]),
            patch("anipyrenamer.cache.get_file_info", side_effect=[None, info]),
            patch(
                "anipyrenamer.cli.build_plan",
                return_value=[RenameItem("/in/b.mkv", "/dest/b.mkv", kind=RenameKind.FILE)],
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 2
    finally:
        sys.argv = orig_argv


def test_cli_wires_conflict_policy_and_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    """--on-conflict / --name-dedupe flow into resolve_destination_conflicts.

    Suffix/dedupe behavior itself is characterized in tests/test_conflicts.py; this
    pins that the CLI passes the parsed flags through to the module seam.
    """
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
    groups = [DiscoveredGroup(video_path="/in/a.mkv", sidecar_paths=())]
    captured: dict[str, object] = {}
    real = cli.resolve_destination_conflicts

    def _spy(plan, *, policy, strategy, case_insensitive=None):  # noqa: ANN001
        captured["policy"] = policy
        captured["strategy"] = strategy
        return real(plan, policy=policy, strategy=strategy, case_insensitive=case_insensitive)

    orig_argv = sys.argv
    try:
        sys.argv = [
            "anipyrenamer",
            "/in",
            "--dry-run",
            "--offline",
            "--on-conflict",
            "suffix",
            "--name-dedupe",
            "hash",
        ]
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", return_value="A" * 32),
            patch("anipyrenamer.cache.get_file_info", return_value=info),
            patch(
                "anipyrenamer.cli.build_plan",
                return_value=[RenameItem("/in/a.mkv", "/dest/a.mkv", kind=RenameKind.FILE)],
            ),
            patch("anipyrenamer.cli.resolve_destination_conflicts", side_effect=_spy),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        assert captured == {"policy": "suffix", "strategy": "hash"}
    finally:
        sys.argv = orig_argv


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


def test_prompt_yes_no_enter_defaults_to_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """MyList-scoped (Y/n) confirm: Enter defaults to yes."""
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert cli._prompt_yes_no("Would you like to set storage?") == "y"


def test_prompt_yes_no_accepts_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """MyList-scoped (Y/n) confirm: 'y' and 'yes' both mean yes."""
    replies = iter(["y", "yes"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))
    assert cli._prompt_yes_no("Would you like to set storage?") == "y"
    assert cli._prompt_yes_no("Would you like to set storage?") == "y"


def test_prompt_yes_no_accepts_no(monkeypatch: pytest.MonkeyPatch) -> None:
    """MyList-scoped (Y/n) confirm: 'n' means no."""
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert cli._prompt_yes_no("Would you like to set storage?") == "n"


def test_prompt_yes_no_rejects_a_and_reprompts(monkeypatch: pytest.MonkeyPatch) -> None:
    """MyList-scoped (Y/n) confirm does NOT accept 'a' (yes-to-all); it re-prompts."""
    replies = iter(["a", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))
    assert cli._prompt_yes_no("Would you like to set storage?") == "n"


# --- Slice 2: characterize the literal confirmation-prompt FORMAT strings (SPEC.md §3).
# The answer/rejection/wiring behaviors are covered above and by
# test_mylist_cli_passes_yes_no_confirm; these capture the prompt text itself,
# which the lambda-based tests ignore.


def test_prompt_confirmation_uses_yna_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """Characterization: the rename-apply confirm prompts with the (Y/n/a) format (SPEC.md §3)."""
    seen: list[str] = []

    def fake_input(prompt: str) -> str:
        seen.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", fake_input)
    _prompt_confirmation("Apply these renames?")
    assert seen == ["Apply these renames? (Y/n/a): "]


def test_prompt_yes_no_uses_yn_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """Characterization: the MyList confirm prompts with (Y/n) and never (Y/n/a) (SPEC.md §3)."""
    seen: list[str] = []

    def fake_input(prompt: str) -> str:
        seen.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", fake_input)
    cli._prompt_yes_no("Would you like to set storage?")
    assert seen == ["Would you like to set storage? (Y/n): "]
    assert "(Y/n/a)" not in seen[0]


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
            patch("anipyrenamer.cache.get_file_info", return_value=info),
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


def test_mylist_cli_passes_yes_no_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """The --mylist path wires the MyList-scoped _prompt_yes_no, not the (Y/n/a) confirm.

    Without this guard, the helper + wizard tests can all pass while the real call
    site keeps the unsafe (Y/n/a) confirm with its yes-to-all 'a'.
    """
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
    captured: dict[str, object] = {}

    class _Result:
        attempted = False
        failed = 0

    def _fake_wizard(*, console, client, file_infos, confirm):  # noqa: ANN001, ARG001
        captured["confirm"] = confirm
        return _Result()

    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "/in", "--dry-run", "--offline", "--mylist"]
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", return_value="A" * 32),
            patch("anipyrenamer.cache.get_file_info", return_value=info),
            patch(
                "anipyrenamer.cli.build_plan",
                return_value=[RenameItem("/in/a.mkv", "/dest/a.mkv", kind=RenameKind.FILE)],
            ),
            patch("anipyrenamer.cli.run_mylist_wizard", side_effect=_fake_wizard),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        assert captured["confirm"] is cli._prompt_yes_no
        assert captured["confirm"] is not cli._prompt_confirmation
    finally:
        sys.argv = orig_argv


# --- Slice 5 (CLI orchestration): FILE 506 -> re-login + bounded single retry (SPEC.md §6) ---


class _RetryClient:
    """Fake AniDB client scripting file_lookup outcomes to drive the real CLI lookup loop.

    Each ``file_lookup`` consumes the next ``(return_value, clears_session)`` from ``script``;
    ``clears_session=True`` mimics a FILE 506 (invalid session). ``login`` (initial + any re-login)
    and ``file_lookup`` calls are counted so the test can prove a bounded single retry.
    """

    def __init__(self, script: list[tuple[object, bool]]) -> None:
        self._script = script
        self._session: str | None = None
        self.login_calls = 0
        self.file_lookup_calls = 0

    def login(self) -> tuple[bool, str]:
        self.login_calls += 1
        self._session = "S"
        return (True, "")

    @property
    def has_session(self) -> bool:
        return self._session is not None

    def file_lookup(self, size: int, ed2k: str) -> object:  # noqa: ARG002
        ret, clears = self._script[self.file_lookup_calls]
        self.file_lookup_calls += 1
        if clears:
            self._session = None
        return ret

    def logout(self) -> None:
        self._session = None

    def encrypt(self) -> tuple[bool, str]:
        return (True, "")

    def disable_encryption(self) -> None:
        pass

    @property
    def encryption_enabled(self) -> bool:
        return False


def _file_info() -> FileInfo:
    return FileInfo(
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


def _run_lookup_with_client(client: _RetryClient) -> None:
    """Run main() online (dry-run) with `client` injected and a forced cache miss, so the real
    AniDB lookup loop (re-login + retry) executes at its real call site."""
    from anipyrenamer.anidb import AniDBConfig

    groups = [DiscoveredGroup(video_path="/in/a.mkv", sidecar_paths=())]
    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "/in", "--dry-run"]
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", return_value="A" * 32),
            patch("anipyrenamer.cache.get_file_info", return_value=None),  # cache miss -> AniDB
            patch("anipyrenamer.cli.build_plan", return_value=[]),
            patch(
                "anipyrenamer.anidb.AniDBConfig.from_env",
                return_value=AniDBConfig("u", "p", "c", "1", 0),
            ),
            patch("anipyrenamer.anidb.AniDBClient", return_value=client),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code in (0, 2)
    finally:
        sys.argv = orig_argv


def test_cli_file_506_reauths_and_retries_once() -> None:
    """FILE 506 (session cleared) triggers exactly one re-login + one retry, and the retry result is used."""
    client = _RetryClient([(None, True), (_file_info(), False)])  # 506, then success
    _run_lookup_with_client(client)
    assert client.file_lookup_calls == 2  # initial attempt + one retry
    assert client.login_calls == 2  # initial login + one re-login


def test_cli_file_506_retry_is_bounded() -> None:
    """If the retry also yields nothing, there is NO further re-login/retry — the retry is bounded."""
    client = _RetryClient([(None, True), (None, True)])  # 506, then still no result
    _run_lookup_with_client(client)
    assert client.file_lookup_calls == 2  # bounded: exactly one retry, no loop
    assert client.login_calls == 2  # exactly one re-login


def test_cli_not_found_does_not_reauth() -> None:
    """An ordinary not-found (session kept) does NOT re-login or retry — distinct from FILE 506."""
    client = _RetryClient([(None, False)])  # not-found, session intact
    _run_lookup_with_client(client)
    assert client.file_lookup_calls == 1  # no retry
    assert client.login_calls == 1  # initial login only; no re-login


def test_rename_apply_keeps_yna_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC #14 (symmetric guard): rename-apply still uses _prompt_confirmation (Y/n/a).

    The apply-renames call site keeps the shared (Y/n/a) confirm and still treats
    'a' as apply, so the MyList change does not ripple into the rename flow.
    """
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
    groups = [DiscoveredGroup(video_path="/in/a.mkv", sidecar_paths=())]
    confirm_messages: list[str] = []

    def _fake_confirm(message: str) -> str:
        confirm_messages.append(message)
        return "a"

    apply_called = {"value": False}

    def _fake_apply_plan(items, db_path, *, dry_run, progress_callback):  # noqa: ANN001, ARG001
        apply_called["value"] = True
        return (1, 0)

    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "/in", "--offline"]
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", return_value="A" * 32),
            patch("anipyrenamer.cache.get_file_info", return_value=info),
            patch(
                "anipyrenamer.cli.build_plan",
                return_value=[RenameItem("/in/a.mkv", "/dest/a.mkv", kind=RenameKind.FILE)],
            ),
            patch("anipyrenamer.cli._prompt_confirmation", _fake_confirm),
            patch("anipyrenamer.cli.apply_plan", _fake_apply_plan),
        ):
            with pytest.raises(SystemExit):
                main()
        assert confirm_messages == ["Apply these renames?"]
        assert apply_called["value"] is True
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


def test_hash_group_returns_correct_tuple(tmp_path: Path) -> None:
    """_hash_group returns (group, size, ed2k) for a test file."""
    video = tmp_path / "test.mkv"
    content = b"hello world" * 100
    video.write_bytes(content)
    group = DiscoveredGroup(video_path=str(video), sidecar_paths=())
    result_group, size, ed2k = _hash_group(group)
    assert result_group is group
    assert size == len(content)
    assert isinstance(ed2k, str)
    assert len(ed2k) == 32


def test_hash_group_with_progress_callback(tmp_path: Path) -> None:
    """_hash_group invokes progress_callback when provided."""
    video = tmp_path / "test.mkv"
    video.write_bytes(b"x" * 1000)
    group = DiscoveredGroup(video_path=str(video), sidecar_paths=())
    calls: list[tuple[int, int]] = []
    _, size, _ = _hash_group(group, progress_callback=lambda br, tot: calls.append((br, tot)))
    assert size == 1000
    assert len(calls) > 0
    assert calls[-1] == (1000, 1000)


def test_sequential_hashing_multiple_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI hashes multiple files one at a time on the main thread (discovery order)."""
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
        DiscoveredGroup(video_path="/in/c.mkv", sidecar_paths=()),
    ]
    compute_calls: list[str] = []

    def _mock_compute(path: str, *, progress_callback: object = None) -> str:
        compute_calls.append(path)
        return "A" * 32

    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "/in", "--dry-run", "--offline"]
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", side_effect=_mock_compute),
            patch("anipyrenamer.cache.get_file_info", return_value=info),
            patch(
                "anipyrenamer.cli.build_plan",
                side_effect=[
                    [RenameItem("/in/a.mkv", "/dest/a.mkv", kind=RenameKind.FILE)],
                    [RenameItem("/in/b.mkv", "/dest/b.mkv", kind=RenameKind.FILE)],
                    [RenameItem("/in/c.mkv", "/dest/c.mkv", kind=RenameKind.FILE)],
                ],
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        assert compute_calls == ["/in/a.mkv", "/in/b.mkv", "/in/c.mkv"]
    finally:
        sys.argv = orig_argv


def test_single_file_main_thread_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Single-file run completes hashing on the main thread (no thread pool)."""
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
    groups = [DiscoveredGroup(video_path="/in/a.mkv", sidecar_paths=())]
    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "/in", "--dry-run", "--offline"]
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", return_value="A" * 32),
            patch("anipyrenamer.cache.get_file_info", return_value=info),
            patch(
                "anipyrenamer.cli.build_plan",
                return_value=[RenameItem("/in/a.mkv", "/dest/a.mkv", kind=RenameKind.FILE)],
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
    finally:
        sys.argv = orig_argv


def test_cli_hashing_progress_row_uses_filename_only(tmp_path: Path) -> None:
    """Yellow per-file hashing row shows basename only, not parent directory names."""
    from rich.progress import Progress

    ancestor = tmp_path / "ancestor" / "deep"
    ancestor.mkdir(parents=True)
    video = ancestor / "episode.mkv"
    video.write_bytes(b"x")

    vp = str(video.resolve())
    groups = [DiscoveredGroup(video_path=vp, sidecar_paths=())]
    descriptions: list[str] = []

    real_add_task = Progress.add_task

    def capture_add_task(self: Progress, description: str, **kwargs: object):  # type: ignore[type-arg]
        descriptions.append(description)
        return real_add_task(self, description, **kwargs)

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
    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", str(tmp_path), "--dry-run", "--offline"]
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli.Progress.add_task", capture_add_task),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", return_value="A" * 32),
            patch("anipyrenamer.cache.get_file_info", return_value=info),
            patch(
                "anipyrenamer.cli.build_plan",
                return_value=[RenameItem(vp, "/dest/a.mkv", kind=RenameKind.FILE)],
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
    finally:
        sys.argv = orig_argv

    yellow_tasks = [d for d in descriptions if "[yellow]" in d]
    assert yellow_tasks
    assert all("ancestor" not in d for d in yellow_tasks)
    assert all("deep" not in d for d in yellow_tasks)
    assert all("episode.mkv" in d for d in yellow_tasks)


def test_clear_cache_uses_shared_hashing_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """--clear-cache path uses the shared _hash_group helper."""
    groups = [
        DiscoveredGroup(video_path="/in/a.mkv", sidecar_paths=()),
        DiscoveredGroup(video_path="/in/b.mkv", sidecar_paths=()),
    ]
    hash_group_calls: list[str] = []

    def _mock_hash_group(
        group: DiscoveredGroup, progress_callback: object = None
    ) -> tuple[DiscoveredGroup, int, str]:
        hash_group_calls.append(group.video_path)
        return (group, 10, "A" * 32)

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
    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "/in", "--clear-cache", "--dry-run", "--offline"]
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli._hash_group", side_effect=_mock_hash_group),
            patch("anipyrenamer.cli.clear_file_anidb_entries", return_value=2),
            patch("anipyrenamer.cache.get_file_info", return_value=info),
            patch(
                "anipyrenamer.cli.build_plan",
                side_effect=[
                    [RenameItem("/in/a.mkv", "/dest/a.mkv", kind=RenameKind.FILE)],
                    [RenameItem("/in/b.mkv", "/dest/b.mkv", kind=RenameKind.FILE)],
                ],
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        assert hash_group_calls == ["/in/a.mkv", "/in/b.mkv"]
    finally:
        sys.argv = orig_argv


def test_keyboard_interrupt_during_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KeyboardInterrupt during hashing calls logout and exits 130."""
    (tmp_path / "a.mkv").write_bytes(b"x")
    (tmp_path / "b.mkv").write_bytes(b"y")
    monkeypatch.setenv("ANIDB_USERNAME", "u")
    monkeypatch.setenv("ANIDB_PASSWORD", "p")
    monkeypatch.delenv("ANIDB_API_KEY", raising=False)
    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", str(tmp_path)]
        with patch("anipyrenamer.anidb.AniDBClient") as MockAniDBClient:
            mock_client = MagicMock()
            MockAniDBClient.return_value = mock_client
            mock_client.encrypt.return_value = (True, "")
            mock_client.login.return_value = (True, "")
            mock_client._session = "fake"
            with patch("anipyrenamer.cli.compute_ed2k", side_effect=KeyboardInterrupt):
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == EXIT_INTERRUPTED
            mock_client.logout.assert_called()
    finally:
        sys.argv = orig_argv


# ---------------------------------------------------------------------------
# Characterization oracle for the usable-cache seam (#28, slice A).
# These three tests pin the CLI-observable behavior of the inline refresh
# and bad-cache repair branches BEFORE they move into cache.py. They must
# pass unchanged before and after the seam lands — do not edit them as part
# of that refactor.
# ---------------------------------------------------------------------------


def _seed_cache_entry(db: Path, *, fid: int, title: str) -> None:
    from anipyrenamer.cache import init_db, set_file_info

    init_db(str(db))
    set_file_info(
        str(db),
        FileInfo(
            fid=fid,
            aid=2,
            eid=3,
            gid=4,
            size=10,
            ed2k="A" * 32,
            quality="high",
            source="TV",
            anime_title=title,
            episode_number="01",
            episode_title="Pilot",
            group_name="Group",
        ),
    )


def _characterization_argv(tmp_path: Path, db: Path, log: Path, *extra: str) -> list[str]:
    return [
        "anipyrenamer",
        str(tmp_path),
        "--dry-run",
        "--db",
        str(db),
        "--debug",
        "--template",
        "%title%%ext%",
        "--log-level",
        "INFO",
        "--log-file",
        str(log),
        *extra,
    ]


def _run_characterization() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0


def test_cli_refresh_cache_bypasses_cached_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """C1: --refresh-cache with a client bypasses the cached entry and refetches."""
    (tmp_path / "a.mkv").write_bytes(b"x")
    db = tmp_path / "cache.sqlite"
    log = tmp_path / "run.log"
    _seed_cache_entry(db, fid=1, title="Show")
    monkeypatch.setenv("ANIDB_USERNAME", "u")
    monkeypatch.setenv("ANIDB_PASSWORD", "p")
    monkeypatch.delenv("ANIDB_API_KEY", raising=False)
    client_info = FileInfo(
        fid=99, aid=2, eid=3, gid=4, size=10, ed2k="A" * 32, quality="high",
        source="TV", anime_title="ClientShow", episode_number="01",
        episode_title="Pilot", group_name="Group",
    )
    groups = [DiscoveredGroup(video_path=str(tmp_path / "a.mkv"), sidecar_paths=())]
    orig_argv = sys.argv
    try:
        sys.argv = _characterization_argv(tmp_path, db, log, "--refresh-cache")
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", return_value="A" * 32),
            patch("anipyrenamer.anidb.AniDBClient") as MockAniDBClient,
        ):
            mock_client = MagicMock()
            MockAniDBClient.return_value = mock_client
            mock_client.encrypt.return_value = (True, "")
            mock_client.login.return_value = (True, "")
            mock_client.file_lookup.return_value = client_info
            _run_characterization()
            mock_client.file_lookup.assert_called_once_with(10, "A" * 32)
    finally:
        sys.argv = orig_argv
    out = capsys.readouterr().out
    assert "Fetched from AniDB" in out
    assert "Using cached AniDB data" not in out  # refresh discards before the debug hit line
    assert "Cached title looks like hash" not in out
    assert "fid=99 lookup_source=anidb" in log.read_text(encoding="utf-8")


def test_cli_hash_looking_cached_title_repairs_with_both_debug_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """C2: hash-looking cached title with a client refetches; debug shows hit line THEN repair line."""
    (tmp_path / "a.mkv").write_bytes(b"x")
    db = tmp_path / "cache.sqlite"
    log = tmp_path / "run.log"
    _seed_cache_entry(db, fid=1, title="e" * 32)
    monkeypatch.setenv("ANIDB_USERNAME", "u")
    monkeypatch.setenv("ANIDB_PASSWORD", "p")
    monkeypatch.delenv("ANIDB_API_KEY", raising=False)
    client_info = FileInfo(
        fid=99, aid=2, eid=3, gid=4, size=10, ed2k="A" * 32, quality="high",
        source="TV", anime_title="ClientShow", episode_number="01",
        episode_title="Pilot", group_name="Group",
    )
    groups = [DiscoveredGroup(video_path=str(tmp_path / "a.mkv"), sidecar_paths=())]
    orig_argv = sys.argv
    try:
        sys.argv = _characterization_argv(tmp_path, db, log)
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", return_value="A" * 32),
            patch("anipyrenamer.anidb.AniDBClient") as MockAniDBClient,
        ):
            mock_client = MagicMock()
            MockAniDBClient.return_value = mock_client
            mock_client.encrypt.return_value = (True, "")
            mock_client.login.return_value = (True, "")
            mock_client.file_lookup.return_value = client_info
            _run_characterization()
            mock_client.file_lookup.assert_called_once_with(10, "A" * 32)
    finally:
        sys.argv = orig_argv
    out = capsys.readouterr().out
    hit_idx = out.find("Using cached AniDB data")
    repair_idx = out.find("Cached title looks like hash")
    assert hit_idx != -1, "expected the cache-hit debug line on the repair path"
    assert repair_idx != -1, "expected the repair debug line"
    assert hit_idx < repair_idx, "hit line must precede repair line (current CLI order)"
    assert "Fetched from AniDB" in out
    assert "fid=99 lookup_source=anidb" in log.read_text(encoding="utf-8")


def test_cli_offline_keeps_hash_looking_cached_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """C3: offline mode uses a hash-looking cached entry as-is (no repair without a refetch path)."""
    (tmp_path / "a.mkv").write_bytes(b"x")
    db = tmp_path / "cache.sqlite"
    log = tmp_path / "run.log"
    _seed_cache_entry(db, fid=7, title="e" * 32)
    groups = [DiscoveredGroup(video_path=str(tmp_path / "a.mkv"), sidecar_paths=())]
    orig_argv = sys.argv
    try:
        sys.argv = _characterization_argv(tmp_path, db, log, "--offline")
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", return_value="A" * 32),
            patch("anipyrenamer.anidb.AniDBClient") as MockAniDBClient,
        ):
            _run_characterization()
            MockAniDBClient.assert_not_called()
    finally:
        sys.argv = orig_argv
    out = capsys.readouterr().out
    assert "Using cached AniDB data" in out
    assert "Using local cache" in out
    assert "Cached title looks like hash" not in out
    assert "fid=7 lookup_source=cache" in log.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# #29: cached entries never expire by age. This test is NOT part of the #28
# oracle above and may be edited as the age-policy contract evolves.
# ---------------------------------------------------------------------------


def test_cli_offline_old_cached_entry_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Offline mode plans from an old cached entry instead of skipping it for age (#29)."""
    from anipyrenamer.cache import time as cache_time

    (tmp_path / "a.mkv").write_bytes(b"x")
    db = tmp_path / "cache.sqlite"
    log = tmp_path / "run.log"
    _seed_cache_entry(db, fid=7, title="Show")
    now = cache_time.time()
    ten_years_seconds = 10 * 365 * 24 * 60 * 60
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "UPDATE file_anidb SET cached_at = ? WHERE size = ? AND ed2k = ?",
            (now - ten_years_seconds, 10, "A" * 32),
        )
        conn.commit()
    groups = [DiscoveredGroup(video_path=str(tmp_path / "a.mkv"), sidecar_paths=())]
    orig_argv = sys.argv
    try:
        sys.argv = _characterization_argv(tmp_path, db, log, "--offline")
        with (
            patch("anipyrenamer.cli.discover", return_value=groups),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", return_value="A" * 32),
            patch("anipyrenamer.anidb.AniDBClient") as MockAniDBClient,
        ):
            _run_characterization()
            MockAniDBClient.assert_not_called()
    finally:
        sys.argv = orig_argv
    out = capsys.readouterr().out
    assert "Using cached AniDB data" in out
    assert "Using local cache" in out
    assert "fid=7 lookup_source=cache" in log.read_text(encoding="utf-8")
