"""Slice 5: explicit-argv allowlist and dispatch-ordering hardening for --doctor.

SPEC.md §8: only --offline, --debug, --log-file, --log-level, --db, and -h/--help
may accompany --doctor. Any positional path or other pipeline flag is a usage
error (exit 2) naming every offender. Classification is by explicitly supplied,
not value-vs-default.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import anipyrenamer.cli as cli


def _run_doctor_argv(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> tuple[int, str]:
    monkeypatch.setattr(cli, "_load_env", lambda: ())
    monkeypatch.setattr(cli, "_configure_cli_logging", lambda **_: None)
    monkeypatch.setattr(
        cli,
        "run_doctor",
        lambda **_: 0,
    )

    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("doctor entered the rename pipeline")

    monkeypatch.setattr(cli, "discover", forbidden)
    monkeypatch.setattr(cli, "init_db", forbidden)

    original_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", *argv]
        with pytest.raises(SystemExit) as exc_info:
            cli.main()
    finally:
        sys.argv = original_argv
    code = exc_info.value.code
    assert isinstance(code, int)
    return code, ""


@pytest.mark.parametrize(
    "extra_argv",
    [
        ["--dry-run"],
        ["-y"],
        ["--yes"],
        ["-t", "custom"],
        ["--template", "custom"],
        ["--folder"],
        ["--folder-template", "custom"],
        ["--plex"],
        ["-d", "dest"],
        ["--dest", "dest"],
        ["--clear-cache"],
        ["--clear-cache-all"],
        ["--on-conflict", "skip"],
        ["--name-dedupe", "counter"],
        ["--preview-format", "table"],
        ["--refresh-cache"],
        ["--mylist"],
    ],
)
def test_doctor_rejects_every_pipeline_flag_including_default_valued(
    extra_argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every non-allowlisted flag is rejected even when its value equals the default."""
    code, _ = _run_doctor_argv(["--doctor", *extra_argv], monkeypatch)
    assert code == 2
    stderr = capsys.readouterr().err
    offender = extra_argv[0]
    assert offender in stderr


def test_doctor_rejects_positional_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _ = _run_doctor_argv(["--doctor", "some/path"], monkeypatch)
    assert code == 2
    stderr = capsys.readouterr().err
    assert "some/path" in stderr


def test_doctor_reports_multiple_offenders_in_one_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _ = _run_doctor_argv(["--doctor", "--dry-run", "--yes", "extra/path"], monkeypatch)
    assert code == 2
    stderr = capsys.readouterr().err
    assert "--dry-run" in stderr
    assert "--yes" in stderr
    assert "extra/path" in stderr


@pytest.mark.parametrize(
    "allowed_argv",
    [
        ["--doctor"],
        ["--doctor", "--offline"],
        ["--doctor", "--debug"],
        ["--doctor", "--log-level", "DEBUG"],
        ["--doctor", "--db", "somewhere.sqlite"],
        ["--doctor", "--offline", "--debug", "--log-level", "INFO", "--db", "x.sqlite"],
    ],
)
def test_doctor_accepts_allowlisted_flag_combinations(
    allowed_argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, _ = _run_doctor_argv(allowed_argv, monkeypatch)
    assert code == 0


def test_doctor_accepts_explicit_log_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_file = str(tmp_path / "doctor.log")
    code, _ = _run_doctor_argv(["--doctor", "--log-file", log_file], monkeypatch)
    assert code == 0


def test_doctor_help_follows_argparse_precedence_and_runs_no_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--doctor --help exits via argparse (0) without ever calling run_doctor."""

    def forbidden_run_doctor(**_: object) -> int:
        raise AssertionError("doctor ran a check despite --help")

    monkeypatch.setattr(cli, "_load_env", lambda: ())
    monkeypatch.setattr(cli, "run_doctor", forbidden_run_doctor)
    original_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "--doctor", "--help"]
        with pytest.raises(SystemExit) as exc_info:
            cli.main()
    finally:
        sys.argv = original_argv
    assert exc_info.value.code == 0


def test_doctor_allowed_dests_documents_help_for_contract_completeness() -> None:
    """The allowlist set names "help" even though argparse always intercepts -h/--help first.

    SPEC.md §8 documents -h/--help as accompanying --doctor; the allowlist set
    should stay internally consistent with that even though the validator
    never actually sees -h/--help (argparse exits during parse_args() first).
    """
    assert "help" in cli._DOCTOR_ALLOWED_DESTS


def test_doctor_logger_setup_failure_exits_1_before_doctor_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-doctor logging setup failure is fatal exit 1, before any doctor check."""

    def failing_logging(**_: object) -> None:
        print("Could not open --log-file", file=sys.stderr)
        sys.exit(1)

    def forbidden_run_doctor(**_: object) -> int:
        raise AssertionError("doctor ran a check despite a logger setup failure")

    monkeypatch.setattr(cli, "_load_env", lambda: ())
    monkeypatch.setattr(cli, "_configure_cli_logging", failing_logging)
    monkeypatch.setattr(cli, "run_doctor", forbidden_run_doctor)

    original_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "--doctor", "--log-file", str(tmp_path / "x.log")]
        with pytest.raises(SystemExit) as exc_info:
            cli.main()
    finally:
        sys.argv = original_argv
    assert exc_info.value.code == 1
