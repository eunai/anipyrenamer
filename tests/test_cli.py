"""CLI tests: --help, no paths, dry-run with no videos, interrupt logout, --plex."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path, WindowsPath
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from anipyrenamer.cli import (
    EXIT_INTERRUPTED,
    _apply_plex_suffix,
    _apply_suffix_conflict_resolution,
    _get_well_known_env_path,
    _hash_group,
    _prompt_confirmation,
    main,
)
from anipyrenamer.models import DiscoveredGroup, FileInfo, RenameItem, RenameKind
from anipyrenamer.naming import DEFAULT_FOLDER_TEMPLATE
from anipyrenamer.validation import flatten_and_validate_folder_renames


def test_load_env_picks_dotenv_from_cwd_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: load .env from cwd/parent chain; do not infer from unrelated dev-repo paths."""
    import anipyrenamer.cli as cli_module

    (tmp_path / ".env").write_text(
        "ANIDB_USERNAME=from_cwd_walk\nANIDB_PASSWORD=from_cwd_pass\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for key in (
        "ANIDB_USERNAME",
        "ANIDB_PASSWORD",
        "ANIDB_UDP_CLIENT",
        "ANIDB_UDP_CLIENTVER",
        "ANIDB_LOCAL_PORT",
        "ANIDB_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(cli_module, "_get_well_known_env_path", lambda: None)
    cli_module._load_env()

    assert os.environ.get("ANIDB_USERNAME") == "from_cwd_walk"
    assert os.environ.get("ANIDB_PASSWORD") == "from_cwd_pass"


def test_load_env_no_cwd_wellknown_cred_tmp_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No .env along cwd chain and disabled well-known => no injected AniDB creds."""

    import anipyrenamer.cli as cli_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_module, "find_dotenv", lambda *a, **k: "")
    for key in (
        "ANIDB_USERNAME",
        "ANIDB_PASSWORD",
        "ANIDB_UDP_CLIENT",
        "ANIDB_UDP_CLIENTVER",
        "ANIDB_LOCAL_PORT",
        "ANIDB_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(cli_module, "_get_well_known_env_path", lambda: None)
    cli_module._load_env()

    assert os.environ.get("ANIDB_USERNAME") in (None, "")
    assert os.environ.get("ANIDB_PASSWORD") in (None, "")


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


# --- Interactive start: no-path scan-path spine (Issue #7) -------------------
#
# Mocking seam: production gates the whole flow on ``sys.stdin.isatty()`` and,
# on a TTY, calls ``questionary.path(<message>).ask()`` behind a lazy
# ``import questionary``. Tests drive the public entry point ``main()`` with no
# positional path and:
#   * patch ``sys.stdin.isatty`` to select the TTY vs non-TTY branch, and
#   * patch ``questionary.path`` to return a stub whose ``.ask()`` yields the
#     simulated operator input — a path string, ``""`` for an empty submit, or
#     ``None`` for a Ctrl-C / Esc cancel (questionary returns ``None`` on cancel).


def _run_main_no_path() -> int:
    """Invoke ``main()`` with a bare ``anipyrenamer`` argv; return the exit code."""
    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer"]
        with pytest.raises(SystemExit) as exc_info:
            main()
        code = exc_info.value.code
        return 0 if code is None else int(code)
    finally:
        sys.argv = orig_argv


def test_no_path_non_tty_prints_help_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No path on a non-TTY stdin keeps legacy behavior: print help, exit 0, no prompt."""
    with patch("sys.stdin.isatty", return_value=False), patch("questionary.path") as q_path:
        code = _run_main_no_path()
    out = capsys.readouterr().out
    assert code == 0
    assert "usage:" in out.lower()  # argparse help, not the pipeline
    assert "Discovery" not in out
    q_path.assert_not_called()  # interactive prompt never reached off a TTY


def test_no_path_tty_valid_path_enters_pipeline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A valid scan path from the TTY prompt is accepted and the normal pipeline runs."""
    stub = MagicMock()
    stub.ask.return_value = str(tmp_path)  # existing (empty) directory
    checkbox_stub = MagicMock()
    checkbox_stub.ask.return_value = ["dry_run"]
    with patch("sys.stdin.isatty", return_value=True), patch(
        "questionary.path", return_value=stub
    ), patch("questionary.checkbox", return_value=checkbox_stub):
        code = _run_main_no_path()
    out = capsys.readouterr().out
    assert code == 0
    assert stub.ask.call_count == 1
    # Pipeline entered with the prompted path: discovery ran and found no videos.
    assert "Discovery" in out
    assert "No video files found." in out
    assert "usage:" not in out.lower()


def test_no_path_tty_nonexistent_path_reprompts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A nonexistent entered path re-prompts; it is never passed to the pipeline."""
    missing = str(tmp_path / "does-not-exist")
    stub = MagicMock()
    # First a missing path (must re-prompt), then empty to bail out without running.
    stub.ask.side_effect = [missing, ""]
    with patch("sys.stdin.isatty", return_value=True), patch(
        "questionary.path", return_value=stub
    ):
        code = _run_main_no_path()
    out = capsys.readouterr().out
    assert code == 0
    assert stub.ask.call_count == 2  # re-prompted after the missing path
    # The missing path must NOT reach discovery (which would say "No video files found.").
    assert "No video files found." not in out
    assert "Discovery" not in out


def test_no_path_tty_empty_path_exits_zero_without_cancelled(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Submitting an empty path bails out with exit 0 and does NOT print 'Cancelled.'."""
    stub = MagicMock()
    stub.ask.return_value = ""
    with patch("sys.stdin.isatty", return_value=True), patch(
        "questionary.path", return_value=stub
    ):
        code = _run_main_no_path()
    out = capsys.readouterr().out
    assert code == 0
    assert stub.ask.call_count == 1  # the scan-path prompt was shown
    assert "usage:" not in out.lower()  # not the legacy help path
    assert "Cancelled." not in out  # empty submit is not the cancel signal
    assert "Discovery" not in out  # pipeline not entered


def test_no_path_tty_cancel_prints_cancelled_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ctrl-C / Esc cancel (questionary returns None) prints 'Cancelled.', exits 0, no traceback."""
    stub = MagicMock()
    stub.ask.return_value = None  # questionary returns None on Ctrl-C / Esc
    with patch("sys.stdin.isatty", return_value=True), patch(
        "questionary.path", return_value=stub
    ):
        code = _run_main_no_path()  # SystemExit captured here => no traceback escapes
    out = capsys.readouterr().out
    assert code == 0
    assert stub.ask.call_count == 1
    assert "usage:" not in out.lower()
    assert "Cancelled." in out
    assert "Discovery" not in out  # pipeline not entered


# --- Interactive start: options checklist (Issue #8) -------------------------
#
# Tracer bullet: after the scan-path prompt collects a valid path, the interactive
# start must present a single options checklist via ``questionary.checkbox`` before
# the pipeline runs. This first test asserts only that the checklist *appears* once,
# through the public ``main()`` entry point, mirroring the issue-#7 seam (patch
# ``sys.stdin.isatty`` + the relevant ``questionary`` call). Default-state, the
# plex/folder invariant, and selection->flag mapping are separate later behaviors.


def test_no_path_tty_shows_options_checklist_after_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """After a valid scan path is entered, the options checklist is presented once."""
    path_stub = MagicMock()
    path_stub.ask.return_value = str(tmp_path)  # existing (empty) directory
    checkbox_stub = MagicMock()
    # questionary.checkbox(...).ask() returns the list of selected option values.
    checkbox_stub.ask.return_value = ["dry_run"]
    with patch("sys.stdin.isatty", return_value=True), patch(
        "questionary.path", return_value=path_stub
    ), patch("questionary.checkbox", return_value=checkbox_stub):
        code = _run_main_no_path()
    assert code == 0
    assert path_stub.ask.call_count == 1  # scan path collected first
    # The options checklist is shown exactly once, after the scan path.
    assert checkbox_stub.ask.call_count == 1


def test_no_path_tty_checklist_cancel_prints_cancelled_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C / Esc on the options checklist cancels cleanly: exit 0, 'Cancelled.', no pipeline.

    Mirrors the scan-path cancel test (test_no_path_tty_cancel_prints_cancelled_and_exits_zero):
    questionary returns ``None`` on Ctrl-C / Esc. Here the scan path is collected successfully,
    then ``questionary.checkbox(...).ask()`` returns ``None`` — the checklist cancel. The run
    must exit 0, print a short ``Cancelled.`` line, and NOT enter the pipeline (no ``Discovery``),
    with no traceback escaping (SystemExit is captured by the helper).
    """
    path_stub = MagicMock()
    path_stub.ask.return_value = str(tmp_path)  # valid path => checklist is reached
    checkbox_stub = MagicMock()
    checkbox_stub.ask.return_value = None  # Ctrl-C / Esc on the checklist

    with patch("sys.stdin.isatty", return_value=True), patch(
        "questionary.path", return_value=path_stub
    ), patch("questionary.checkbox", return_value=checkbox_stub):
        code = _run_main_no_path()  # SystemExit captured here => no traceback escapes

    out = capsys.readouterr().out
    assert code == 0
    assert path_stub.ask.call_count == 1  # scan path collected first
    assert checkbox_stub.ask.call_count == 1  # checklist shown, then cancelled
    assert "Cancelled." in out
    assert "Discovery" not in out  # pipeline not entered after checklist cancel


def test_no_path_tty_checklist_shows_dry_run_default_notice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Interactive start shows a visible notice that dry-run is on by default.

    Issue #8 and docs/internal/modules/cli.md §7 require, alongside the pre-checked dry-run
    option, "a visible notice that dry-run is on by default and nothing is renamed until it is
    turned off." No exact wording is mandated, so this asserts the documented phrase
    "dry-run is on by default" (case-insensitive) as the smallest stable substring. This notice
    is part of interactive start (shown around the checklist) and is distinct from the later
    runtime ``Dry run; no files changed.`` message — ``discover`` returns no groups so the run
    exits 0 before any planning/apply output.
    """
    path_stub = MagicMock()
    path_stub.ask.return_value = str(tmp_path)
    checkbox_stub = MagicMock()
    checkbox_stub.ask.return_value = ["dry_run"]

    with patch("sys.stdin.isatty", return_value=True), patch(
        "questionary.path", return_value=path_stub
    ), patch("questionary.checkbox", return_value=checkbox_stub), patch(
        "anipyrenamer.cli.discover", return_value=[]
    ):
        code = _run_main_no_path()

    out = capsys.readouterr().out
    assert code == 0
    # The default-on notice is shown during interactive start (before any planning output).
    assert "dry-run is on by default" in out.lower()


def test_no_path_tty_checklist_validate_rejects_plex_without_folder(
    tmp_path: Path,
) -> None:
    """The checklist passes a validate callback that forbids Plex-on/Folder-off submission.

    Plex-without-folder is the impossible state (issue #8). questionary.checkbox accepts a
    ``validate(selected) -> bool | str`` callback; returning a non-True string rejects the
    submission and re-prompts. This test inspects the validate kwarg passed at the
    questionary.checkbox seam and exercises it directly:
      * ["plex"]            -> rejected (validate returns a non-empty error string)
      * ["plex", "folder"]  -> accepted (True)
      * ["dry_run"], []     -> accepted (True)
    ``discover`` returns no groups so main() exits 0 right after the prompts.
    """
    path_stub = MagicMock()
    path_stub.ask.return_value = str(tmp_path)
    checkbox_stub = MagicMock()
    checkbox_stub.ask.return_value = ["dry_run"]

    with patch("sys.stdin.isatty", return_value=True), patch(
        "questionary.path", return_value=path_stub
    ), patch("questionary.checkbox", return_value=checkbox_stub) as checkbox_mock, patch(
        "anipyrenamer.cli.discover", return_value=[]
    ):
        code = _run_main_no_path()

    assert code == 0
    checkbox_mock.assert_called_once()
    validate = checkbox_mock.call_args.kwargs["validate"]

    # Impossible state is rejected with a human-readable reason.
    plex_only = validate(["plex"])
    assert plex_only is not True
    assert isinstance(plex_only, str) and plex_only

    # Valid combinations are accepted.
    assert validate(["plex", "folder"]) is True
    assert validate(["dry_run"]) is True
    assert validate([]) is True


def test_no_path_tty_checklist_item_shape_and_default_state(
    tmp_path: Path,
) -> None:
    """The options checklist is built with the intended values, default-check, and Plex nesting.

    Locks the checklist item shape at the ``questionary.checkbox`` seam (only ``checkbox`` is
    patched; the real ``questionary.Choice`` objects are constructed and inspected):
      * exactly four options with values dry_run, folder, plex, offline (in that order),
      * ``dry_run`` is checked by default,
      * the Plex option's displayed label is visually nested (indented) under folder, while
        ``folder`` is not indented.
    ``discover`` returns no groups so ``main()`` exits 0 right after the prompts without
    needing the AniDB/plan machinery.
    """
    path_stub = MagicMock()
    path_stub.ask.return_value = str(tmp_path)
    checkbox_stub = MagicMock()
    checkbox_stub.ask.return_value = ["dry_run"]

    with patch("sys.stdin.isatty", return_value=True), patch(
        "questionary.path", return_value=path_stub
    ), patch("questionary.checkbox", return_value=checkbox_stub) as checkbox_mock, patch(
        "anipyrenamer.cli.discover", return_value=[]
    ):
        code = _run_main_no_path()

    assert code == 0
    checkbox_mock.assert_called_once()
    choices = checkbox_mock.call_args.kwargs["choices"]
    by_value = {c.value: c for c in choices}

    # Exactly the four intended option values, in order.
    assert [c.value for c in choices] == ["dry_run", "folder", "plex", "offline"]
    # Dry-run is pre-checked by default.
    assert by_value["dry_run"].checked is True
    # Plex is visually nested under folder: indented label vs. non-indented folder label.
    assert by_value["plex"].title.startswith(" ")
    assert not by_value["folder"].title.startswith(" ")


def test_no_path_tty_checklist_folder_selection_enables_folder_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selecting 'folder' in the interactive checklist drives the pipeline in folder-rename mode.

    Observable seam: ``build_plan`` is the single place where folder mode surfaces — it is
    called with a non-None ``folder_template`` only when folder renaming is on. Here the
    operator picks ``folder`` from the checklist, so the resolved selections must turn folder
    mode on and ``build_plan`` must receive a folder template. A SKIP plan item keeps the run
    on the no-renames-to-apply exit (code 0) without touching the apply confirmation seam.
    """
    import anipyrenamer.cli as cli_module

    # Keep AniDB out of the loop: no env creds, no .env load => client stays None (cache only).
    monkeypatch.setattr(cli_module, "_load_env", lambda: None)
    monkeypatch.delenv("ANIDB_USERNAME", raising=False)
    monkeypatch.delenv("ANIDB_PASSWORD", raising=False)

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

    path_stub = MagicMock()
    path_stub.ask.return_value = str(tmp_path)
    checkbox_stub = MagicMock()
    # Operator keeps dry-run on and additionally selects folder renaming.
    checkbox_stub.ask.return_value = ["dry_run", "folder"]

    captured: dict[str, object] = {}

    def _capture_build_plan(
        group: object,
        info_arg: object,
        template: str,
        dest: object = None,
        folder_template: str | None = None,
    ) -> list[RenameItem]:
        captured["folder_template"] = folder_template
        return [
            RenameItem(
                old_path=str(tmp_path / "a.mkv"),
                new_path="(skip)",
                kind=RenameKind.SKIP,
                anime_type="",
            )
        ]

    with patch("sys.stdin.isatty", return_value=True), patch(
        "questionary.path", return_value=path_stub
    ), patch("questionary.checkbox", return_value=checkbox_stub), patch(
        "anipyrenamer.cli.discover", return_value=groups
    ), patch("anipyrenamer.cli.get_file_size", return_value=10), patch(
        "anipyrenamer.cli.compute_ed2k", return_value="A" * 32
    ), patch("anipyrenamer.cli.get_file_info", return_value=info), patch(
        "anipyrenamer.cli.build_plan", side_effect=_capture_build_plan
    ):
        code = _run_main_no_path()

    assert code == 0
    assert checkbox_stub.ask.call_count == 1
    # Folder mode selected => build_plan must receive a folder template (not None).
    assert captured.get("folder_template") is not None


def test_no_path_tty_checklist_plex_selection_enables_plex_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selecting 'plex' in the interactive checklist runs the pipeline in Plex mode.

    Observable seam: ``--plex`` makes the pipeline pass ``build_plan`` a folder template
    carrying the Plex/HAMA AniDB tag ``[anidb-%aid%]`` (via ``_apply_plex_suffix``) — the
    plex-specific marker, distinct from plain folder mode. The operator picks ``plex`` from the
    checklist, so the resolved selections must turn Plex mode on and ``build_plan`` must receive
    that tagged folder template. A SKIP plan item routes the run to the no-renames exit (code 0).
    """
    import anipyrenamer.cli as cli_module

    # Keep AniDB out of the loop: no env creds, no .env load => client stays None (cache only).
    monkeypatch.setattr(cli_module, "_load_env", lambda: None)
    monkeypatch.delenv("ANIDB_USERNAME", raising=False)
    monkeypatch.delenv("ANIDB_PASSWORD", raising=False)

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

    path_stub = MagicMock()
    path_stub.ask.return_value = str(tmp_path)
    checkbox_stub = MagicMock()
    checkbox_stub.ask.return_value = ["dry_run", "plex"]

    captured: dict[str, object] = {}

    def _capture_build_plan(
        group: object,
        info_arg: object,
        template: str,
        dest: object = None,
        folder_template: str | None = None,
    ) -> list[RenameItem]:
        captured["folder_template"] = folder_template
        return [
            RenameItem(
                old_path=str(tmp_path / "a.mkv"),
                new_path="(skip)",
                kind=RenameKind.SKIP,
                anime_type="",
            )
        ]

    with patch("sys.stdin.isatty", return_value=True), patch(
        "questionary.path", return_value=path_stub
    ), patch("questionary.checkbox", return_value=checkbox_stub), patch(
        "anipyrenamer.cli.discover", return_value=groups
    ), patch("anipyrenamer.cli.get_file_size", return_value=10), patch(
        "anipyrenamer.cli.compute_ed2k", return_value="A" * 32
    ), patch("anipyrenamer.cli.get_file_info", return_value=info), patch(
        "anipyrenamer.cli.build_plan", side_effect=_capture_build_plan
    ):
        code = _run_main_no_path()

    assert code == 0
    folder_template = captured.get("folder_template")
    # plex selected => build_plan receives a folder template carrying the Plex AniDB tag.
    assert folder_template is not None
    assert "[anidb-%aid%]" in folder_template


def test_no_path_tty_checklist_offline_selection_enables_offline_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Selecting 'offline' in the interactive checklist runs the pipeline in offline mode.

    Observable seam: the AniDB client is resolved only inside ``if not args.offline:``
    (cli.py). With no credentials in the environment, that block prints the cache-only
    fallback notice ``ANIDB_USERNAME/ANIDB_PASSWORD not set; using cache only.`` — a line that
    is emitted *only when offline is off*. Selecting ``offline`` must skip the whole block,
    exactly as ``--offline`` would, so that notice must be absent. A SKIP plan item routes the
    run to the no-renames exit (code 0).
    """
    import anipyrenamer.cli as cli_module

    # No creds + no .env load: without offline, the block would print the cache-only notice.
    monkeypatch.setattr(cli_module, "_load_env", lambda: None)
    monkeypatch.delenv("ANIDB_USERNAME", raising=False)
    monkeypatch.delenv("ANIDB_PASSWORD", raising=False)

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

    path_stub = MagicMock()
    path_stub.ask.return_value = str(tmp_path)
    checkbox_stub = MagicMock()
    checkbox_stub.ask.return_value = ["offline"]

    with patch("sys.stdin.isatty", return_value=True), patch(
        "questionary.path", return_value=path_stub
    ), patch("questionary.checkbox", return_value=checkbox_stub), patch(
        "anipyrenamer.cli.discover", return_value=groups
    ), patch("anipyrenamer.cli.get_file_size", return_value=10), patch(
        "anipyrenamer.cli.compute_ed2k", return_value="A" * 32
    ), patch("anipyrenamer.cli.get_file_info", return_value=info), patch(
        "anipyrenamer.cli.build_plan",
        return_value=[
            RenameItem(
                old_path=str(tmp_path / "a.mkv"),
                new_path="(skip)",
                kind=RenameKind.SKIP,
                anime_type="",
            )
        ],
    ):
        code = _run_main_no_path()

    out = capsys.readouterr().out
    assert code == 0
    # offline selected => AniDB resolution skipped; the not-offline cache-only notice is absent.
    assert "ANIDB_USERNAME/ANIDB_PASSWORD not set; using cache only." not in out


def test_no_path_tty_checklist_dry_run_selection_enables_dry_run_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Selecting 'dry_run' in the interactive checklist runs the pipeline in dry-run mode.

    Observable seam: with at least one FILE item planned, dry-run mode prints
    ``Dry run; no files changed.`` and exits before the apply confirmation / ``apply_plan``
    (cli.py). The operator picks only ``dry_run`` from the checklist, so the run must behave
    exactly as ``--dry-run`` would: the dry-run message appears and apply is never reached.
    ``_prompt_confirmation`` (forced to abort) and ``apply_plan`` are patched purely as
    deterministic, no-filesystem safety nets for the (currently unmapped) non-dry-run path.
    """
    import anipyrenamer.cli as cli_module

    # Keep AniDB out of the loop: no env creds, no .env load => client stays None (cache only).
    monkeypatch.setattr(cli_module, "_load_env", lambda: None)
    monkeypatch.delenv("ANIDB_USERNAME", raising=False)
    monkeypatch.delenv("ANIDB_PASSWORD", raising=False)

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

    path_stub = MagicMock()
    path_stub.ask.return_value = str(tmp_path)
    checkbox_stub = MagicMock()
    checkbox_stub.ask.return_value = ["dry_run"]

    with patch("sys.stdin.isatty", return_value=True), patch(
        "questionary.path", return_value=path_stub
    ), patch("questionary.checkbox", return_value=checkbox_stub), patch(
        "anipyrenamer.cli.discover", return_value=groups
    ), patch("anipyrenamer.cli.get_file_size", return_value=10), patch(
        "anipyrenamer.cli.compute_ed2k", return_value="A" * 32
    ), patch("anipyrenamer.cli.get_file_info", return_value=info), patch(
        "anipyrenamer.cli.build_plan",
        return_value=[
            RenameItem(
                old_path=str(tmp_path / "a.mkv"),
                new_path=str(tmp_path / "out.mkv"),
                kind=RenameKind.FILE,
            )
        ],
    ), patch("anipyrenamer.cli._prompt_confirmation", return_value="n"), patch(
        "anipyrenamer.cli.apply_plan", return_value=(0, 0)
    ) as apply_mock:
        code = _run_main_no_path()

    out = capsys.readouterr().out
    assert code == 0
    # dry_run selected => dry-run behavior: message printed, apply never reached.
    assert "Dry run; no files changed." in out
    apply_mock.assert_not_called()


def test_cli_flag_folder_preserved_when_interactive_collects_only_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit ``--folder`` CLI flag survives interactive start (path-only collection).

    When the operator runs ``anipyrenamer --folder`` with no positional path, interactive
    start fires only to collect the missing scan path. The checklist mapping must not erase a
    flag the user already supplied on the command line: even if ``folder`` is not in the
    checklist result, folder mode stays on. Observable seam: ``build_plan`` still receives a
    non-None ``folder_template``. A SKIP plan item routes the run to the no-renames exit
    (code 0) without touching the apply confirmation seam.
    """
    import anipyrenamer.cli as cli_module

    # Keep AniDB out of the loop: no env creds, no .env load => client stays None (cache only).
    monkeypatch.setattr(cli_module, "_load_env", lambda: None)
    monkeypatch.delenv("ANIDB_USERNAME", raising=False)
    monkeypatch.delenv("ANIDB_PASSWORD", raising=False)

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

    path_stub = MagicMock()
    path_stub.ask.return_value = str(tmp_path)
    checkbox_stub = MagicMock()
    # Operator did NOT pick folder in the checklist; it was supplied via --folder on the CLI.
    checkbox_stub.ask.return_value = ["dry_run"]

    captured: dict[str, object] = {}

    def _capture_build_plan(
        group: object,
        info_arg: object,
        template: str,
        dest: object = None,
        folder_template: str | None = None,
    ) -> list[RenameItem]:
        captured["folder_template"] = folder_template
        return [
            RenameItem(
                old_path=str(tmp_path / "a.mkv"),
                new_path="(skip)",
                kind=RenameKind.SKIP,
                anime_type="",
            )
        ]

    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "--folder"]
        with patch("sys.stdin.isatty", return_value=True), patch(
            "questionary.path", return_value=path_stub
        ), patch("questionary.checkbox", return_value=checkbox_stub), patch(
            "anipyrenamer.cli.discover", return_value=groups
        ), patch("anipyrenamer.cli.get_file_size", return_value=10), patch(
            "anipyrenamer.cli.compute_ed2k", return_value="A" * 32
        ), patch("anipyrenamer.cli.get_file_info", return_value=info), patch(
            "anipyrenamer.cli.build_plan", side_effect=_capture_build_plan
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
    finally:
        sys.argv = orig_argv

    # --folder supplied on the CLI must not be erased by the checklist mapping.
    assert captured.get("folder_template") is not None


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
            patch("anipyrenamer.cli.get_file_info", return_value=info),
            patch(
                "anipyrenamer.cli.build_plan",
                return_value=[
                    RenameItem(
                        str(tmp_path / "a.mkv"), str(tmp_path / "out.mkv"), kind=RenameKind.FILE
                    )
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


def test_cli_warns_when_api_key_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Follow-up review P1-A/P2-5: ANIDB_API_KEY unset emits the unencrypted-credentials panel."""
    import anipyrenamer.cli as cli_module

    (tmp_path / "a.mkv").write_bytes(b"x")
    monkeypatch.setenv("ANIDB_USERNAME", "u")
    monkeypatch.setenv("ANIDB_PASSWORD", "p")
    monkeypatch.delenv("ANIDB_API_KEY", raising=False)
    monkeypatch.setattr(cli_module, "_load_env", lambda: None)

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
                with pytest.raises(SystemExit):
                    main()
        captured = capsys.readouterr().out
        assert "Credentials will be sent unencrypted over UDP" in captured
        assert "Set ANIDB_API_KEY" in captured
        mock_client.encrypt.assert_not_called()
    finally:
        sys.argv = orig_argv


def test_cli_warns_when_encrypt_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Follow-up review P1-A/P2-5: encrypt() failure emits fallback panel and disables encryption."""
    import anipyrenamer.cli as cli_module

    (tmp_path / "a.mkv").write_bytes(b"x")
    monkeypatch.setenv("ANIDB_USERNAME", "u")
    monkeypatch.setenv("ANIDB_PASSWORD", "p")
    monkeypatch.setenv("ANIDB_API_KEY", "k")
    monkeypatch.setattr(cli_module, "_load_env", lambda: None)

    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", str(tmp_path)]
        with patch("anipyrenamer.anidb.AniDBClient") as MockAniDBClient:
            mock_client = MagicMock()
            MockAniDBClient.return_value = mock_client
            mock_client.encrypt.return_value = (False, "309 API PASSWORD NOT DEFINED")
            mock_client.login.return_value = (True, "")
            mock_client._session = "fake"
            with patch("anipyrenamer.cli.compute_ed2k", side_effect=KeyboardInterrupt):
                with pytest.raises(SystemExit):
                    main()
        captured = capsys.readouterr().out
        assert "Encryption setup failed" in captured
        assert "falling back to unencrypted mode" in captured
        mock_client.encrypt.assert_called_once()
        mock_client.disable_encryption.assert_called_once()
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
            patch("anipyrenamer.cli.get_file_info", side_effect=[None, info]),
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


def test_cli_preview_format_json_payload_unchanged() -> None:
    """JSON preview remains a flat list of plan item dicts."""
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
        anime_type="tv",
    )
    group = DiscoveredGroup(video_path="/in/a.mkv", sidecar_paths=())
    item = RenameItem("/in/a.mkv", "/out/Show 01.mkv", kind=RenameKind.FILE, anime_type="tv")
    recorded = Console(record=True, force_terminal=True, color_system="truecolor", width=200)
    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "/in", "--dry-run", "--offline", "--preview-format", "json"]
        with (
            patch("anipyrenamer.cli.Console", return_value=recorded),
            patch("anipyrenamer.cli.discover", return_value=[group]),
            patch("anipyrenamer.cli.get_file_size", return_value=10),
            patch("anipyrenamer.cli.compute_ed2k", return_value="A" * 32),
            patch("anipyrenamer.cli.get_file_info", return_value=info),
            patch("anipyrenamer.cli.build_plan", return_value=[item]),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
    finally:
        sys.argv = orig_argv

    output = recorded.export_text()
    json_start = output.index("[\n")
    json_end = output.index("\nDry run", json_start)
    payload = json.loads(output[json_start:json_end])
    assert payload == [
        {
            "old_path": "/in/a.mkv",
            "new_path": "/out/Show 01.mkv",
            "kind": "file",
            "anime_type": "tv",
        }
    ]


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
            patch("anipyrenamer.cli.get_file_info", return_value=info),
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
            patch("anipyrenamer.cli.get_file_info", return_value=info),
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
            patch("anipyrenamer.cli.get_file_info", return_value=info),
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
            patch("anipyrenamer.cli.get_file_info", return_value=info),
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


def test_keyboard_interrupt_during_hashing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
