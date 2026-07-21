"""Version consistency tests."""

from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

import anipyrenamer.cli as cli
from anipyrenamer import __version__


def test_package_version_matches_pyproject() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match is not None
    assert match.group(1) == __version__


def _install_forbidden_sentinels(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if --version reaches env loading, logging, doctor, or DB init."""

    def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("--version must not reach this boundary")

    monkeypatch.setattr(cli, "_load_env", _forbidden)
    monkeypatch.setattr(cli, "_configure_cli_logging", _forbidden)
    monkeypatch.setattr(cli, "run_doctor", _forbidden)
    monkeypatch.setattr(cli, "init_db", _forbidden)


def test_cli_version_flag_prints_installed_version_with_no_side_effects(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Issue #58 tracer bullet: `anipyrenamer --version` at the public CLI seam.

    Prefers installed distribution metadata (patched here to a known value) and
    exits before environment loading, logging setup, doctor dispatch, or database
    initialization -- reaching any of those sentinels fails the test.
    """
    received_names: list[str] = []

    def _stub_dist_version(name: str) -> str:
        received_names.append(name)
        return "9.9.9-test"

    monkeypatch.setattr(cli, "_dist_version", _stub_dist_version)
    _install_forbidden_sentinels(monkeypatch)

    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "--version"]
        with pytest.raises(SystemExit) as exc_info:
            cli.main()
        assert exc_info.value.code == 0
    finally:
        sys.argv = orig_argv

    captured = capsys.readouterr()
    assert captured.out == "anipyrenamer 9.9.9-test\n"
    assert captured.err == ""
    # Regression guard: a wrong distribution name would still print *a* version
    # string, so the identity passed to the metadata boundary must be pinned too.
    assert received_names == ["anipyrenamer"]


def test_cli_version_flag_falls_back_to_source_version_when_uninstalled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Slice 2 (#58): an uninstalled source tree (no distribution metadata) falls back
    to the package's own `__version__`, without reaching any pipeline sentinel."""

    def _raise_not_found(name: str) -> str:
        assert name == "anipyrenamer"
        raise PackageNotFoundError(name)

    monkeypatch.setattr(cli, "_dist_version", _raise_not_found)
    _install_forbidden_sentinels(monkeypatch)

    orig_argv = sys.argv
    try:
        sys.argv = ["anipyrenamer", "--version"]
        with pytest.raises(SystemExit) as exc_info:
            cli.main()
        assert exc_info.value.code == 0
    finally:
        sys.argv = orig_argv

    captured = capsys.readouterr()
    assert captured.out == f"anipyrenamer {__version__}\n"
    assert captured.err == ""
