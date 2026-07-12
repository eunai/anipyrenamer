"""Standalone, redacted preflight checks for the command-line interface."""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rich.console import Console

from anipyrenamer.anidb import AniDBClient, AniDBConfig, PING_TIMEOUT, PingOutcome
from anipyrenamer.cache import CacheProbeOutcome, get_db_path, probe_cache_operational
from anipyrenamer.permissions import warn_if_shared_directory_windows, warn_if_world_readable

DoctorSeverity = Literal["pass", "warn", "fail", "info"]
REPORT_WIDTH = 10_000


@dataclass(frozen=True)
class DoctorResult:
    """One sanitized doctor row."""

    name: str
    severity: DoctorSeverity
    detail: str
    runbook_heading: str | None = None


def _severity_weight(severity: DoctorSeverity) -> int:
    if severity == "fail":
        return 3
    if severity == "warn":
        return 2
    if severity == "pass":
        return 1
    return 0


def _aggregate_exit(results: list[DoctorResult]) -> int:
    worst = max((_severity_weight(result.severity) for result in results), default=0)
    return {0: 0, 1: 0, 2: 2, 3: 1}[worst]


def _env_result(env_sources: tuple[Path, ...]) -> DoctorResult:
    if not env_sources:
        return DoctorResult(
            ".env discovery", "pass", "none found; using environment variables directly"
        )
    exposed: list[Path] = []
    for path in env_sources:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            warn_if_world_readable(path)
            warn_if_shared_directory_windows(path)
        if caught:
            exposed.append(path)
    paths = ", ".join(str(path) for path in env_sources)
    if exposed:
        exposed_paths = ", ".join(str(path) for path in exposed)
        return DoctorResult(
            ".env discovery",
            "warn",
            f"resolved sources: {paths} (exposed: {exposed_paths})",
            "File permissions (recommended)",
        )
    return DoctorResult(".env discovery", "pass", f"resolved source: {paths}")


def _credential_result() -> DoctorResult:
    missing = [name for name in ("ANIDB_USERNAME", "ANIDB_PASSWORD") if not os.environ.get(name)]

    def state(name: str, *, empty_uses_default: bool) -> str:
        if name not in os.environ:
            return "default"
        if empty_uses_default and not os.environ[name]:
            return "default"
        return "set"

    defaults = ", ".join(
        (
            f"ANIDB_UDP_CLIENT={state('ANIDB_UDP_CLIENT', empty_uses_default=False)}",
            f"ANIDB_UDP_CLIENTVER={state('ANIDB_UDP_CLIENTVER', empty_uses_default=False)}",
            f"ANIDB_LOCAL_PORT={state('ANIDB_LOCAL_PORT', empty_uses_default=True)}",
        )
    )
    if missing:
        return DoctorResult(
            "Credentials",
            "warn",
            f"missing: {', '.join(missing)}; {defaults}",
            "Configuration locations",
        )
    return DoctorResult(
        "Credentials",
        "pass",
        f"ANIDB_USERNAME and ANIDB_PASSWORD are set; {defaults}",
    )


def _encryption_result() -> DoctorResult:
    if os.environ.get("ANIDB_API_KEY"):
        return DoctorResult("Encryption", "pass", "ENCRYPT will be attempted at connect")
    return DoctorResult(
        "Encryption",
        "warn",
        "ANIDB_API_KEY is unset; plaintext UDP may be used",
        "Encryption (AniDB UDP ENCRYPT)",
    )


def _cache_result(db_path: str | None) -> DoctorResult:
    """Report the #40 bounded, non-persistent cache operational probe outcome."""
    resolved = get_db_path(db_path)
    probe = probe_cache_operational(resolved)
    if probe.outcome == CacheProbeOutcome.OPERATIONAL:
        return DoctorResult("Cache", "pass", probe.detail)
    if probe.outcome == CacheProbeOutcome.INCONCLUSIVE:
        return DoctorResult("Cache", "warn", probe.detail, "Failure signatures")
    return DoctorResult("Cache", "fail", probe.detail, "File permissions (recommended)")


def _reachability_result(*, offline: bool) -> DoctorResult:
    if offline:
        return DoctorResult("AniDB reachability", "info", "skipped (--offline)")
    client = AniDBClient(AniDBConfig.from_env())
    try:
        outcome: PingOutcome = client.ping_reachability()
    finally:
        client.close()
    if outcome.status == "reachable":
        return DoctorResult(
            "AniDB reachability",
            "pass",
            f"reachable ({outcome.reply_code} PONG)",
        )
    if outcome.status == "unreachable":
        detail = "unreachable"
        if outcome.reply_code is not None:
            detail += f" (reply code {outcome.reply_code})"
    elif outcome.status == "timeout":
        detail = f"no reply within {PING_TIMEOUT:g}s"
    elif outcome.status == "socket_error":
        detail = "UDP socket unavailable"
    else:
        detail = "unrecognized AniDB reply"
    return DoctorResult("AniDB reachability", "warn", detail, "Failure signatures")


def _render(result: DoctorResult, console: Console) -> None:
    if result.severity == "pass":
        glyph = chr(0x2713)
    elif result.severity == "fail":
        glyph = chr(0x2717)
    elif result.severity == "warn":
        glyph = "!"
    else:
        glyph = "-"
    line = f"{glyph} {result.name}: {result.detail}"
    if result.runbook_heading is not None and result.severity in ("warn", "fail"):
        line += f" (see runbook: {result.runbook_heading})"
    console.print(line)


def run_doctor(
    *,
    db_path: str | None,
    offline: bool,
    env_sources: tuple[Path, ...] = (),
    console: Console | None = None,
) -> int:
    """Run the buffered doctor report and return its standalone exit code."""
    output = console or Console(width=REPORT_WIDTH)
    results = [
        _env_result(env_sources),
        _credential_result(),
        _encryption_result(),
        _cache_result(db_path),
        _reachability_result(offline=offline),
    ]
    output.print("anipyrenamer doctor")
    for result in results:
        _render(result, output)
    return _aggregate_exit(results)
