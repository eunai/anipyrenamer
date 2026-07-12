"""Tracer tests for the standalone doctor preflight."""

from __future__ import annotations

import sys
import os
import re
import warnings
from io import StringIO
from pathlib import Path

import pytest

import anipyrenamer.cli as cli
import anipyrenamer.doctor as doctor
from rich.console import Console


def test_doctor_offline_reports_before_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The real CLI dispatches doctor without calling discover or init_db."""
    monkeypatch.delenv("ANIDB_USERNAME", raising=False)
    monkeypatch.delenv("ANIDB_PASSWORD", raising=False)
    monkeypatch.delenv("ANIDB_API_KEY", raising=False)
    monkeypatch.setattr(cli, "_load_env", lambda: None)
    monkeypatch.setattr(cli, "_configure_cli_logging", lambda **_: None)

    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("doctor entered the rename pipeline")

    monkeypatch.setattr(cli, "discover", forbidden)
    monkeypatch.setattr(cli, "init_db", forbidden)

    original_argv = sys.argv
    try:
        sys.argv = [
            "anipyrenamer",
            "--doctor",
            "--offline",
            "--db",
            str(tmp_path / "cache.sqlite"),
        ]
        with pytest.raises(SystemExit) as exc_info:
            cli.main()
    finally:
        sys.argv = original_argv

    assert exc_info.value.code == 2
    output = capsys.readouterr().out
    rows = output.splitlines()
    assert len(rows) == 6
    assert rows[0] == "anipyrenamer doctor"
    assert [row.split(":", 1)[0] for row in rows[1:]] == [
        "✓ .env discovery",
        "! Credentials",
        "! Encryption",
        "✓ Cache",
        "- AniDB reachability",
    ]
    assert "skipped (--offline)" in rows[5]
    assert "see runbook: Configuration locations" in rows[2]
    assert "see runbook: Encryption (AniDB UDP ENCRYPT)" in rows[3]


def test_doctor_warns_for_exposed_resolved_env_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolved dotenv exposure is a warning and never prints dotenv values."""
    env_path = tmp_path / ".env"
    exposed: list[Path] = []

    def mark_exposed(path: object) -> None:
        exposed.append(Path(path))
        warnings.warn("exposed test path", UserWarning, stacklevel=1)

    monkeypatch.setattr(
        doctor,
        "warn_if_world_readable",
        mark_exposed,
        raising=False,
    )
    monkeypatch.setattr(doctor, "warn_if_shared_directory_windows", lambda _: None, raising=False)

    output = StringIO()
    code = doctor.run_doctor(
        db_path=str(tmp_path / "cache.sqlite"),
        offline=True,
        env_sources=(env_path,),
        console=Console(file=output, width=10_000, color_system=None),
    )

    env_line = output.getvalue().splitlines()[1]
    assert code == 2
    assert env_line.startswith("! .env discovery:")
    assert str(env_path) in env_line
    assert "dotenv-secret" not in output.getvalue()
    assert exposed == [env_path]


def test_doctor_reports_all_exposed_env_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple resolved sources retain paths and use the worse exposure severity."""
    sources = (tmp_path / "project" / ".env", tmp_path / "profile" / ".env")

    def mark_exposed(path: object) -> None:
        warnings.warn(f"exposed {path}", UserWarning, stacklevel=1)

    monkeypatch.setattr(doctor, "warn_if_world_readable", mark_exposed, raising=False)
    monkeypatch.setattr(doctor, "warn_if_shared_directory_windows", lambda _: None, raising=False)
    output = StringIO()
    doctor.run_doctor(
        db_path=str(tmp_path / "cache.sqlite"),
        offline=True,
        env_sources=sources,
        console=Console(file=output, width=10_000, color_system=None),
    )

    env_line = output.getvalue().splitlines()[1]
    assert env_line.startswith("! .env discovery:")
    assert all(str(source) in env_line for source in sources)


def test_doctor_reports_mixed_exposed_and_safe_env_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed sources retain every path while warning on the exposed subset."""
    exposed = tmp_path / "project" / ".env"
    safe = tmp_path / "profile" / ".env"

    def warn_only_exposed(path: object) -> None:
        if Path(path) == exposed:
            warnings.warn("exposed test path", UserWarning, stacklevel=1)

    monkeypatch.setattr(doctor, "warn_if_world_readable", warn_only_exposed, raising=False)
    monkeypatch.setattr(doctor, "warn_if_shared_directory_windows", lambda _: None, raising=False)
    output = StringIO()
    doctor.run_doctor(
        db_path=str(tmp_path / "cache.sqlite"),
        offline=True,
        env_sources=(exposed, safe),
        console=Console(file=output, width=10_000, color_system=None),
    )

    env_line = output.getvalue().splitlines()[1]
    assert env_line == (
        f"! .env discovery: resolved sources: {exposed}, {safe}"
        " (exposed: "
        f"{exposed}"
        ") (see runbook: File permissions (recommended))"
    )


def test_doctor_maps_actual_windows_exposure_to_env_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real Windows permission helper drives the doctor warning row."""
    import anipyrenamer.permissions as permissions

    monkeypatch.setattr(permissions.sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\Francis")
    monkeypatch.setenv("APPDATA", r"C:\Users\Francis\AppData\Roaming")
    source = Path(r"C:\Users\FrancisEvil\config\.env")
    output = StringIO()
    doctor.run_doctor(
        db_path=str(tmp_path / "cache.sqlite"),
        offline=True,
        env_sources=(source,),
        console=Console(file=output, width=10_000, color_system=None),
    )

    env_line = output.getvalue().splitlines()[1]
    assert env_line.startswith("! .env discovery:")
    assert str(source) in env_line
    assert env_line.endswith("(see runbook: File permissions (recommended))")


def test_doctor_encryption_reports_capability_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An API key reports planned ENCRYPT use without claiming active protection."""
    monkeypatch.setenv("ANIDB_USERNAME", "username-secret")
    monkeypatch.setenv("ANIDB_PASSWORD", "password-secret")
    monkeypatch.setenv("ANIDB_API_KEY", "api-key-secret")
    output = StringIO()
    doctor.run_doctor(
        db_path=None,
        offline=True,
        console=Console(file=output, width=10_000, color_system=None),
    )

    encryption_line = output.getvalue().splitlines()[3]
    assert encryption_line == "✓ Encryption: ENCRYPT will be attempted at connect"
    assert all(
        secret not in output.getvalue()
        for secret in ("username-secret", "password-secret", "api-key-secret")
    )
    assert "active" not in encryption_line
    assert "verified" not in encryption_line


def test_doctor_reports_credential_defaults_without_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credential defaults are named as defaults or set, never rendered as values."""
    monkeypatch.setenv("ANIDB_USERNAME", "username-secret")
    monkeypatch.setenv("ANIDB_PASSWORD", "password-secret")
    monkeypatch.delenv("ANIDB_UDP_CLIENT", raising=False)
    monkeypatch.delenv("ANIDB_UDP_CLIENTVER", raising=False)
    monkeypatch.delenv("ANIDB_LOCAL_PORT", raising=False)
    output = StringIO()
    doctor.run_doctor(
        db_path=None,
        offline=True,
        console=Console(file=output, width=10_000, color_system=None),
    )

    credentials_line = output.getvalue().splitlines()[2]
    assert credentials_line == (
        "✓ Credentials: ANIDB_USERNAME and ANIDB_PASSWORD are set; "
        "ANIDB_UDP_CLIENT=default, ANIDB_UDP_CLIENTVER=default, ANIDB_LOCAL_PORT=default"
    )
    assert all(secret not in output.getvalue() for secret in ("username-secret", "password-secret"))


def test_doctor_reports_nonempty_configuration_values_as_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-empty client configuration is reported as set without values."""
    monkeypatch.setenv("ANIDB_USERNAME", "username-secret")
    monkeypatch.setenv("ANIDB_PASSWORD", "password-secret")
    monkeypatch.setenv("ANIDB_UDP_CLIENT", "client-secret")
    monkeypatch.setenv("ANIDB_UDP_CLIENTVER", "version-secret")
    monkeypatch.setenv("ANIDB_LOCAL_PORT", "9876")
    output = StringIO()
    doctor.run_doctor(
        db_path=None,
        offline=True,
        console=Console(file=output, width=10_000, color_system=None),
    )

    credentials_line = output.getvalue().splitlines()[2]
    assert "ANIDB_UDP_CLIENT=set" in credentials_line
    assert "ANIDB_UDP_CLIENTVER=set" in credentials_line
    assert "ANIDB_LOCAL_PORT=set" in credentials_line
    assert all(
        secret not in output.getvalue() for secret in ("client-secret", "version-secret", "9876")
    )


def test_doctor_reports_empty_configuration_values_by_effective_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty client values are set, while empty local port uses its effective default."""
    monkeypatch.setenv("ANIDB_USERNAME", "u")
    monkeypatch.setenv("ANIDB_PASSWORD", "p")
    monkeypatch.setenv("ANIDB_UDP_CLIENT", "")
    monkeypatch.setenv("ANIDB_UDP_CLIENTVER", "")
    monkeypatch.setenv("ANIDB_LOCAL_PORT", "")
    output = StringIO()
    doctor.run_doctor(
        db_path=None,
        offline=True,
        console=Console(file=output, width=10_000, color_system=None),
    )

    credentials_line = output.getvalue().splitlines()[2]
    assert "ANIDB_UDP_CLIENT=set" in credentials_line
    assert "ANIDB_UDP_CLIENTVER=set" in credentials_line
    assert "ANIDB_LOCAL_PORT=default" in credentials_line


def test_doctor_report_has_ansi_stable_text(tmp_path: Path) -> None:
    """TTY/color configuration does not alter the report text."""
    rich_output = StringIO()
    plain_output = StringIO()
    no_color_output = StringIO()
    doctor.run_doctor(
        db_path=str(tmp_path / "rich.sqlite"),
        offline=True,
        console=Console(
            file=rich_output,
            width=10_000,
            color_system="standard",
            force_terminal=True,
        ),
    )
    doctor.run_doctor(
        db_path=str(tmp_path / "plain.sqlite"),
        offline=True,
        console=Console(file=plain_output, width=10_000, color_system=None),
    )
    doctor.run_doctor(
        db_path=str(tmp_path / "no-color.sqlite"),
        offline=True,
        console=Console(
            file=no_color_output,
            width=10_000,
            color_system="standard",
            force_terminal=True,
            no_color=True,
        ),
    )

    ansi_stripped = re.sub(r"\x1b\[[0-9;]*m", "", rich_output.getvalue())
    assert ansi_stripped == plain_output.getvalue()
    assert re.sub(r"\x1b\[[0-9;]*m", "", no_color_output.getvalue()) == plain_output.getvalue()


def test_doctor_maps_ping_reachability_without_protocol_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Online doctor uses the transport seam and renders only bounded outcome text."""

    class FakeOutcome:
        status = "reachable"
        reply_code = 300

    class FakeClient:
        created: list[object] = []

        def __init__(self, config: object) -> None:
            self.config = config
            self.ping_calls = 0
            self.created.append(self)

        def ping_reachability(self) -> FakeOutcome:
            self.ping_calls += 1
            return FakeOutcome()

        def close(self) -> None:
            return None

    class FakeConfig:
        @classmethod
        def from_env(cls) -> object:
            return object()

    monkeypatch.setattr(doctor, "AniDBClient", FakeClient, raising=False)
    monkeypatch.setattr(doctor, "AniDBConfig", FakeConfig, raising=False)
    monkeypatch.setenv("ANIDB_USERNAME", "username-secret")
    monkeypatch.setenv("ANIDB_PASSWORD", "password-secret")
    monkeypatch.setenv("ANIDB_API_KEY", "api-key-secret")
    output = StringIO()

    code = doctor.run_doctor(
        db_path=None,
        offline=False,
        console=Console(file=output, width=10_000, color_system=None),
    )

    reachability_line = output.getvalue().splitlines()[5]
    assert code == 0
    assert reachability_line == "✓ AniDB reachability: reachable (300 PONG)"
    assert FakeClient.created[0].ping_calls == 1


def test_doctor_offline_does_not_construct_reachability_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ForbiddenClient:
        def __init__(self, config: object) -> None:  # noqa: ARG002
            raise AssertionError("offline doctor attempted AniDB reachability")

    monkeypatch.setattr(doctor, "AniDBClient", ForbiddenClient, raising=False)
    output = StringIO()
    doctor.run_doctor(
        db_path=None,
        offline=True,
        console=Console(file=output, width=10_000, color_system=None),
    )
    assert "- AniDB reachability: skipped (--offline)" in output.getvalue()


@pytest.mark.parametrize(
    ("status", "reply_code", "detail"),
    [
        ("unreachable", 500, "unreachable (reply code 500)"),
        ("malformed", None, "unrecognized AniDB reply"),
        ("timeout", None, "no reply within 3s"),
        ("socket_error", None, "UDP socket unavailable"),
    ],
)
def test_doctor_maps_bounded_ping_failures(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    reply_code: int | None,
    detail: str,
) -> None:
    class FakeOutcome:
        pass

    class FakeClient:
        def __init__(self, config: object) -> None:  # noqa: ARG002
            return None

        def ping_reachability(self) -> FakeOutcome:
            outcome = FakeOutcome()
            outcome.status = status
            outcome.reply_code = reply_code
            return outcome

        def close(self) -> None:
            return None

    class FakeConfig:
        @classmethod
        def from_env(cls) -> object:
            return object()

    monkeypatch.setattr(doctor, "AniDBClient", FakeClient, raising=False)
    monkeypatch.setattr(doctor, "AniDBConfig", FakeConfig, raising=False)
    output = StringIO()
    code = doctor.run_doctor(
        db_path=None,
        offline=False,
        console=Console(file=output, width=10_000, color_system=None),
    )
    reachability_line = output.getvalue().splitlines()[5]
    assert code == 2
    assert reachability_line == f"! AniDB reachability: {detail} (see runbook: Failure signatures)"


def test_doctor_cache_result_uses_real_operational_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doctor's Cache row reflects the real #40 probe, not a hardcoded tracer path."""
    from anipyrenamer.cache import CacheProbeOutcome, CacheProbeResult

    calls: list[str] = []

    def fake_probe(db_path: str) -> CacheProbeResult:
        calls.append(db_path)
        return CacheProbeResult(CacheProbeOutcome.INCONCLUSIVE, "sanitized detail only")

    monkeypatch.setattr(doctor, "probe_cache_operational", fake_probe, raising=False)
    db_path = str(tmp_path / "cache.sqlite")
    output = StringIO()
    code = doctor.run_doctor(
        db_path=db_path,
        offline=True,
        console=Console(file=output, width=10_000, color_system=None),
    )

    cache_line = output.getvalue().splitlines()[4]
    assert calls == [db_path]
    assert code == 2
    assert cache_line == "! Cache: sanitized detail only (see runbook: Failure signatures)"


def test_doctor_cache_fail_uses_permissions_runbook_heading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anipyrenamer.cache import CacheProbeOutcome, CacheProbeResult

    def fake_probe(db_path: str) -> CacheProbeResult:  # noqa: ARG001
        return CacheProbeResult(CacheProbeOutcome.UNUSABLE, "sanitized failure detail")

    monkeypatch.setattr(doctor, "probe_cache_operational", fake_probe, raising=False)
    output = StringIO()
    code = doctor.run_doctor(
        db_path=None,
        offline=True,
        console=Console(file=output, width=10_000, color_system=None),
    )

    cache_line = output.getvalue().splitlines()[4]
    assert code == 1
    assert cache_line == (
        "✗ Cache: sanitized failure detail (see runbook: File permissions (recommended))"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits are not meaningful on Windows")
def test_doctor_maps_world_readable_env_to_warning_row(tmp_path: Path) -> None:
    """The real Unix permission helper drives the doctor warning row."""
    env_path = tmp_path / ".env"
    env_path.write_text("ANIDB_PASSWORD=dotenv-secret", encoding="utf-8")
    os.chmod(env_path, 0o644)
    output = StringIO()
    doctor.run_doctor(
        db_path=str(tmp_path / "cache.sqlite"),
        offline=True,
        env_sources=(env_path,),
        console=Console(file=output, width=10_000, color_system=None),
    )

    env_line = output.getvalue().splitlines()[1]
    assert env_line.startswith("! .env discovery:")
    assert str(env_path) in env_line
    assert "dotenv-secret" not in output.getvalue()
    assert env_line.endswith("(see runbook: File permissions (recommended))")
