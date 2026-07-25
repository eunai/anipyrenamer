"""CLI: discover, hash, lookup, plan, preview, apply (dry-run / Y/n). Output: the Quiet Ledger stream + run-summary footer (ledger.py)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from pathlib import Path
from typing import Any, Callable

from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.markup import escape as rich_escape

from anipyrenamer import __version__ as _SOURCE_VERSION
from anipyrenamer.apply import apply_plan
from anipyrenamer.cache import (
    CacheOutcome,
    clear_file_anidb_cache,
    clear_file_anidb_entries,
    get_db_path,
    init_db,
    get_usable_file_info,
    set_file_info,
)
from anipyrenamer.conflicts import resolve_destination_conflicts
from anipyrenamer.discovery import discover, get_file_size
from anipyrenamer.doctor import run_doctor
from anipyrenamer.ed2k import compute_ed2k
from anipyrenamer.ledger import Ledger, RunOutcome
from anipyrenamer.models import DiscoveredGroup, FileInfo, RenameItem, RenameKind
from anipyrenamer.mylist import run_mylist_wizard
from anipyrenamer.naming import DEFAULT_FILE_TEMPLATE, DEFAULT_FOLDER_TEMPLATE
from anipyrenamer.plan import build_plan
from anipyrenamer.permissions import warn_if_world_readable
from anipyrenamer.progress_overlay import LinkState, ProgressOverlay
from anipyrenamer.validation import flatten_and_validate_folder_renames


def _get_well_known_env_path() -> Path | None:
    """Path to .env in a well-known config dir for global installs (Windows: %%APPDATA%%/anipyrenamer, Unix: ~/.config/anipyrenamer)."""
    if os.name == "nt":
        apd = os.environ.get("APPDATA")
        if not apd:
            return None
        return Path(apd) / "anipyrenamer" / ".env"
    return Path.home() / ".config" / "anipyrenamer" / ".env"


def _load_env() -> tuple[Path, ...]:
    """Load ``.env``: walk **upward from process cwd**, then well-known config path.

    Uses ``find_dotenv(usecwd=True)`` so discovery matches operator expectations when
    running from an arbitrary folder (editable installs no longer implicitly pick up a
    dev-repo ``.env`` via ``python-dotenv``'s default anchor). Loads do not override
    already-set environment variables (override=False).
    """
    sources: list[Path] = []
    dotted_usecwd = find_dotenv(usecwd=True) or ""

    if dotted_usecwd:
        sources.append(Path(dotted_usecwd))
        load_dotenv(dotted_usecwd)
        # Warn against the *resolved* discovered path (may be a parent .env),
        # not a literal cwd .env, which may be a different or non-existent file.
        discovered = Path(dotted_usecwd)
        if discovered.exists():
            warn_if_world_readable(discovered)
    well_known = _get_well_known_env_path()
    if well_known is not None:
        if well_known.exists():
            sources.append(well_known)
            warn_if_world_readable(well_known)
        load_dotenv(well_known)
    return tuple(sources)


_LOG = logging.getLogger("anipyrenamer.cli")


def _configure_cli_logging(*, level_name: str, log_file: str | None) -> None:
    """Configure the ``anipyrenamer.*`` logging namespace (stderr + optional UTF-8 file)."""
    pkg = logging.getLogger("anipyrenamer")
    pkg.handlers.clear()
    pkg.propagate = False

    level = getattr(logging, level_name.upper(), logging.WARNING)
    pkg.setLevel(level)

    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    stderr_h = logging.StreamHandler(sys.stderr)
    stderr_h.setLevel(level)
    stderr_h.setFormatter(fmt)
    pkg.addHandler(stderr_h)

    if not log_file:
        return

    path = Path(log_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Could not create log file directory ({path.parent}): {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        file_h = logging.FileHandler(path, encoding="utf-8")
    except OSError as exc:
        print(f"Could not open --log-file {path}: {exc}", file=sys.stderr)
        sys.exit(1)
    file_h.setLevel(level)
    file_h.setFormatter(fmt)
    pkg.addHandler(file_h)


# Exit code when user interrupts (e.g. Ctrl+C)
EXIT_INTERRUPTED = 130
# Exit code when completed with partial failures/skips/conflicts (spec §6)
EXIT_PARTIAL = 2

PLEX_SUFFIX = " [anidb-%aid%]"


def _apply_plex_suffix(template: str) -> str:
    """Insert the Plex/ASS/HAMA AniDB-id tag before %ext% (or append if no %ext%)."""
    if "%ext%" in template:
        return template.replace("%ext%", f"{PLEX_SUFFIX}%ext%", 1)
    return template + PLEX_SUFFIX


def _hash_group(
    group: DiscoveredGroup,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[DiscoveredGroup, int, str]:
    """Hash a single group's video file (shared helper for CLI and --clear-cache prehash)."""
    size = get_file_size(group.video_path)
    ed2k = compute_ed2k(group.video_path, progress_callback=progress_callback)
    return (group, size, ed2k)


def _disconnect_anidb(client: Any, console: Console, had_session: bool) -> None:
    """Log out from AniDB and print disconnect message only when we had a session."""
    if client is not None:
        client.logout()
    if had_session:
        console.print("[cyan]✓ Disconnected from AniDB.[/cyan]")


def _prompt_confirmation(message: str) -> str:
    """
    Prompt user with standardized confirmation format: (Y/n/a).

    Returns:
        "y" -> yes (including Enter default)
        "n" -> no
        "a" -> yes to all remaining
    """
    while True:
        try:
            raw = input(f"{message} (Y/n/a): ").strip().lower()
        except EOFError:
            return "y"

        if raw == "":
            return "y"
        if raw in ("y", "yes"):
            return "y"
        if raw == "n":
            return "n"
        if raw == "a":
            return "a"
        print("Please enter Y, n, or a.")


def _prompt_yes_no(message: str) -> str:
    """
    MyList-scoped confirmation prompt: (Y/n).

    Unlike :func:`_prompt_confirmation`, this prompt has no "yes to all" answer:
    every MyList confirmation is an independent yes/no, so ``a`` (and any other
    input) is rejected and re-prompts.

    Returns:
        "y" -> yes (including Enter default)
        "n" -> no
    """
    while True:
        try:
            raw = input(f"{message} (Y/n): ").strip().lower()
        except EOFError:
            return "y"

        if raw == "":
            return "y"
        if raw in ("y", "yes"):
            return "y"
        if raw == "n":
            return "n"
        print("Please enter Y or n.")


# Doctor's explicit-argv allowlist (SPEC.md §8): only these dests may accompany
# --doctor. "help" is listed for contract completeness even though argparse's
# own -h/--help handling always exits during parse_args(), before this
# allowlist ever runs. Revalidate this set whenever a new parser argument is added.
_DOCTOR_ALLOWED_DESTS = frozenset(
    {"doctor", "offline", "debug", "log_file", "log_level", "db", "help"}
)


def _validate_doctor_allowlist(parser: argparse.ArgumentParser, argv: list[str]) -> None:
    """Reject any explicitly supplied option or positional not in the doctor allowlist.

    Classification is by *explicitly supplied* option strings on the raw argv,
    not by whether the parsed value differs from its default: a value equal to
    its default but typed on the command line is still an offender. Value
    tokens belonging to an allowlisted option (e.g. the level after
    ``--log-level``) are consumed and skipped, not evaluated as positionals.
    """
    option_to_action: dict[str, argparse.Action] = {}
    for action in parser._actions:  # noqa: SLF001 - argparse exposes no public option-string map
        for option_string in action.option_strings:
            option_to_action[option_string] = action

    offenders: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        looks_like_option = token.startswith("-") and len(token) > 1
        if not looks_like_option:
            # A bare non-flag token (including a lone "-") is a positional path;
            # doctor accepts none.
            offenders.append(token)
            index += 1
            continue
        name, _, inline_value = token.partition("=")
        matched_action = option_to_action.get(name)
        if matched_action is None or matched_action.dest not in _DOCTOR_ALLOWED_DESTS:
            offenders.append(name)
            index += 1
            continue
        takes_value = matched_action.nargs != 0 and not isinstance(
            matched_action,
            (argparse._StoreTrueAction, argparse._StoreFalseAction, argparse._HelpAction),
        )
        if takes_value and not inline_value:
            index += 1  # skip the option's value token, e.g. "DEBUG" after --log-level
        index += 1

    if offenders:
        parser.error(
            "--doctor accepts only --offline, --debug, --log-file, --log-level, --db, "
            "and -h/--help; unexpected: " + ", ".join(offenders)
        )


class _VersionAction(argparse.Action):
    """Print ``<prog> <version>`` and exit; resolves the version lazily, only when invoked.

    Registering ``--version`` via ``action="version"`` would force eager evaluation of
    its ``version=`` string at parser-construction time, on every CLI invocation --
    including a hard failure if distribution metadata is ever unavailable. Resolving
    inside ``__call__`` keeps the lookup scoped to an actual ``--version`` invocation.
    """

    def __init__(
        self,
        option_strings: list[str],
        dest: str = argparse.SUPPRESS,
        default: str = argparse.SUPPRESS,
        help: str | None = None,  # noqa: A002 - matches argparse.Action's parameter name
    ) -> None:
        super().__init__(
            option_strings=option_strings, dest=dest, default=default, nargs=0, help=help
        )

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        try:
            resolved_version = _dist_version("anipyrenamer")
        except PackageNotFoundError:
            # Uninstalled source tree (e.g. running from a checkout without an
            # editable install): fall back to the package's own __version__.
            resolved_version = _SOURCE_VERSION
        print(f"{parser.prog} {resolved_version}")
        parser.exit()


def main() -> None:
    """Run full pipeline: discover, hash, lookup, plan, preview, apply."""
    parser = argparse.ArgumentParser(
        prog="anipyrenamer",
        description="Rename anime files using ED2K hash and AniDB.",
    )
    parser.add_argument(
        "--version",
        action=_VersionAction,
        help="Show the installed anipyrenamer version and exit.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="path",
        help="Files or folders to scan.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show rename plan only; do not apply.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Auto-accept apply.",
    )
    parser.add_argument(
        "-t",
        "--template",
        default=DEFAULT_FILE_TEMPLATE,
        help="Episode file naming template (default: %(default)s).",
    )
    parser.add_argument(
        "--folder",
        action="store_true",
        help="Also rename the direct parent folder using the series folder template.",
    )
    parser.add_argument(
        "--folder-template",
        default=DEFAULT_FOLDER_TEMPLATE,
        help="Series folder naming template when --folder is used (default: %(default)s).",
    )
    parser.add_argument(
        "--plex",
        action="store_true",
        help=(
            "Append [anidb-<aid>] to the folder name for Plex / Absolute Series Scanner (ASS) "
            "/ HAMA compatibility (implies --folder). Does not support season-based numbering."
        ),
    )
    parser.add_argument(
        "-d",
        "--dest",
        default=None,
        help="Destination root (default: in-place).",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite cache path.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use cache only; no AniDB API calls.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Run read-only setup checks and exit.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Log AniDB request/response and cache hits (for debugging).",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear AniDB cache only for the file(s) or folder(s) being scanned (force refetch for those).",
    )
    parser.add_argument(
        "--clear-cache-all",
        action="store_true",
        help="Clear entire local AniDB file cache before run.",
    )
    parser.add_argument(
        "--on-conflict",
        choices=("skip", "suffix", "fail"),
        default="skip",
        help="Conflict behavior for existing/colliding destinations (default: skip).",
    )
    parser.add_argument(
        "--name-dedupe",
        choices=("none", "counter", "hash"),
        default="counter",
        help="Deterministic dedupe strategy used with --on-conflict=suffix (default: counter).",
    )
    parser.add_argument(
        "--preview-format",
        choices=("table", "json"),
        default="table",
        help="Rename plan preview output format (default: table).",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Bypass the cache and refetch AniDB data for scanned files when online.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable the live progress overlay shown during hashing on an interactive terminal.",
    )
    parser.add_argument(
        "--mylist",
        action="store_true",
        help="After pipeline completion, run interactive MyList update wizard.",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="WARNING",
        help=(
            "Minimum level for structured ``anipyrenamer.*`` log records on stderr "
            "(default: %(default)s). Does not silence Rich/console output."
        ),
    )
    parser.add_argument(
        "--log-file",
        default=None,
        metavar="PATH",
        help="Append structured logs (UTF-8) to this file (same levels as --log-level).",
    )
    args = parser.parse_args()

    env_sources = _load_env()
    _configure_cli_logging(level_name=args.log_level, log_file=args.log_file)

    if args.doctor:
        _validate_doctor_allowlist(parser, sys.argv[1:])
        sys.exit(
            run_doctor(
                db_path=args.db,
                offline=args.offline,
                env_sources=env_sources,
            )
        )

    if not args.paths:
        parser.print_help()
        sys.exit(0)

    console = Console()
    ledger = Ledger(console)
    db_path = get_db_path(args.db)
    init_db(db_path)
    if args.clear_cache_all:
        clear_file_anidb_cache(db_path)
        console.print("[dim]Entire AniDB file cache cleared.[/dim]")

    groups = discover(args.paths)
    _LOG.info("phase=discovery group_count=%d", len(groups))
    ledger.discover(len(groups))
    if not groups:
        console.print("[yellow]No video files found.[/yellow]")
        ledger.footer(RunOutcome.NO_MATCHES)
        sys.exit(RunOutcome.NO_MATCHES.exit_code)

    precomputed_hashes: dict[str, tuple[int, str]] = {}
    if args.clear_cache:
        # Silent prehash (no transient Progress on the ledger path, SPEC §3).
        entries: list[tuple[int, str]] = []
        for group in groups:
            _, size, ed2k = _hash_group(group)
            entries.append((size, ed2k))
            precomputed_hashes[group.video_path] = (size, ed2k)
        n = clear_file_anidb_entries(db_path, entries)
        console.print(f"[dim]Cleared AniDB cache for {n} file(s) in this scan.[/dim]")

    # Resolve AniDB client only when not offline
    client: Any = None
    had_session = False
    if not args.offline:
        from anipyrenamer.anidb import AniDBConfig, AniDBClient

        cfg = AniDBConfig.from_env()
        if cfg.username and cfg.password:
            client = AniDBClient(cfg, debug=args.debug)
            api_key = (cfg.api_key or "").strip()
            if not api_key:
                console.print(
                    Panel(
                        "[bold yellow]Credentials will be sent unencrypted over UDP.[/]\n"
                        "Set ANIDB_API_KEY in .env to enable AES-128 session encryption.\n"
                        "Use a dedicated AniDB account and avoid untrusted networks.",
                        title="Security Warning",
                        border_style="yellow",
                    )
                )
            else:
                ok, enc_msg = client.encrypt()
                if not ok:
                    console.print(
                        Panel(
                            "[bold yellow]Encryption setup failed; falling back to unencrypted mode.[/]\n"
                            "Verify ANIDB_API_KEY in .env and your AniDB UDP API key settings.\n\n"
                            f"[dim]{rich_escape(enc_msg)}[/dim]",
                            title="Security Warning",
                            border_style="yellow",
                        )
                    )
                    client.disable_encryption()
            try:
                ok, msg = client.login()
                if ok:
                    console.print("[green]✓ Connected to AniDB.[/green]")
                    had_session = True
                if not ok:
                    if msg and "555" in msg and "BANNED" in msg.upper():
                        console.print(
                            "[red]AniDB returned: account or IP is temporarily banned.[/red] "
                            "Wait before retrying; use [bold]--offline[/bold] to use cache only."
                        )
                    else:
                        console.print("[red]AniDB login failed.[/red]")
                    _disconnect_anidb(client, console, False)
                    sys.exit(1)
            except TimeoutError:
                console.print(
                    "[red]AniDB connection timed out.[/red] "
                    "You may be rate-limited or temporarily banned. "
                    "Try again later or use [bold]--offline[/bold] to use cache only."
                )
                _disconnect_anidb(client, console, False)
                sys.exit(1)
        else:
            console.print(
                "[yellow]ANIDB_USERNAME/ANIDB_PASSWORD not set; using cache only.[/yellow]"
            )

    # Ensure LOGOUT is sent on any exit (Ctrl+C, exception, normal)
    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is not None:

        def _sigterm_to_interrupt(signum: int, frame: Any) -> None:
            raise KeyboardInterrupt

        signal.signal(sigterm, _sigterm_to_interrupt)

    try:
        _run_after_anidb_ready(
            args, client, had_session, console, ledger, db_path, groups, precomputed_hashes
        )
    except KeyboardInterrupt:
        # Degraded interrupt form: the verdict line alone (SPEC §3/§4).
        ledger.verdict_only(RunOutcome.INTERRUPTED)
        sys.exit(RunOutcome.INTERRUPTED.exit_code)


def _run_after_anidb_ready(
    args: argparse.Namespace,
    client: Any,
    had_session: bool,
    console: Console,
    ledger: Ledger,
    db_path: str,
    groups: list[Any],
    precomputed_hashes: dict[str, tuple[int, str]],
) -> None:
    """Run hashing, lookup, plan, preview, apply. Guarantees AniDB logout in finally."""
    try:
        _do_hashing_lookup_plan_apply(
            args, client, console, ledger, db_path, groups, precomputed_hashes
        )
    finally:
        _disconnect_anidb(client, console, had_session)


def _progress_enabled(args: argparse.Namespace, console: Console) -> bool:
    """Whether the hash+look progress overlay may run.

    TTY-only and strictly additive: suppressed under ``--no-progress``, ``--preview-format
    json`` (before any terminal evaluation), a non-interactive stdout, or a dumb terminal. When
    this is false the overlay emits nothing and the hashing callback path stays ``None``.
    """
    if args.no_progress:
        return False
    if args.preview_format == "json":
        return False
    return console.is_terminal and not console.is_dumb_terminal


def _link_state(client: Any) -> LinkState | None:
    """Snapshot the AniDB connection state for the overlay's lookup row (None when offline)."""
    if client is None:
        return None
    return LinkState(
        session=client.has_session,
        encryption=client.encryption_enabled,
        throttle_seconds=client.next_send_interval,
    )


def _do_hashing_lookup_plan_apply(
    args: argparse.Namespace,
    client: Any,
    console: Console,
    ledger: Ledger,
    db_path: str,
    groups: list[Any],
    precomputed_hashes: dict[str, tuple[int, str]],
) -> None:
    """Hashing and lookup, then plan, preview, and optionally apply."""
    _LOG.info("phase=hash_lookup group_count=%d", len(groups))
    all_items: list[tuple[list[RenameItem], str]] = []  # (items, batch_id placeholder)
    resolved_infos: list[FileInfo] = []

    max_consecutive_timeouts = 3
    consecutive_timeouts = 0
    anidb_aborted = False
    cached_count = 0
    fetched_count = 0
    no_match_count = 0

    # The progress overlay (TTY-only, transient) sits above the untouched Quiet Ledger during
    # hash+look: per-file progress is shown live, then cleared, and the permanent counter line
    # (SPEC §3) prints once the phase settles. Off-TTY / json / --no-progress: no overlay bytes
    # and no per-chunk callback work. Only --debug cache lines and error lines print inline.
    with ProgressOverlay(
        console, total=len(groups), enabled=_progress_enabled(args, console)
    ) as overlay:
        for ordinal, group in enumerate(groups, start=1):
            path_str = str(group.video_path)
            basename = Path(path_str).name
            lookup_source: str | None = None
            cached_hash = precomputed_hashes.get(group.video_path)
            if cached_hash is not None:
                size, ed2k = cached_hash
            else:
                size = get_file_size(group.video_path)
                overlay.begin_hash(ordinal, basename, size)
                # Feed the callback only while the overlay is live; a suppressed or
                # cached-only run hashes with no callback work at all.
                progress_callback = overlay.on_progress if overlay.active else None
                ed2k = compute_ed2k(group.video_path, progress_callback=progress_callback)
            overlay.advance_overall(ordinal)
            lookup = get_usable_file_info(
                db_path, size, ed2k, refresh=args.refresh_cache, allow_refetch=client is not None
            )
            info = lookup.info
            if lookup.outcome is not CacheOutcome.MISS and args.debug:
                console.print(
                    f"[dim][debug] Using cached AniDB data for size={size} ed2k={ed2k[:16]}…[/dim]"
                )
            if lookup.outcome is CacheOutcome.REPAIR and args.debug:
                console.print(
                    "[dim][debug] Cached title looks like hash; refetching from AniDB.[/dim]"
                )
            if info is not None:
                lookup_source = "cache"
                cached_count += 1
            if info is None and client and not anidb_aborted:
                try:
                    info = client.file_lookup(size, ed2k)
                    if info is not None:
                        lookup_source = "anidb"
                    if info is None and not client.has_session:
                        client.login()
                        info = client.file_lookup(size, ed2k)
                        if info is not None:
                            lookup_source = "anidb"
                    consecutive_timeouts = 0
                except TimeoutError:
                    consecutive_timeouts += 1
                    console.print(
                        f"[red]AniDB lookup timed out for {rich_escape(path_str)}; skipping.[/red]"
                    )
                    if consecutive_timeouts >= max_consecutive_timeouts:
                        console.print(
                            f"[red]{max_consecutive_timeouts} consecutive AniDB timeouts; "
                            "skipping remaining AniDB lookups.[/red] "
                            "Try again later or use [bold]--offline[/bold] to use cache only."
                        )
                        anidb_aborted = True
                if info is not None:
                    set_file_info(db_path, info)
                    fetched_count += 1
            if info is None:
                no_match_count += 1
                overlay.settle_lookup(
                    cached_count, fetched_count, no_match_count, _link_state(client)
                )
                _LOG.info(
                    "phase=lookup basename=%s fid=0 lookup_source=%s",
                    basename,
                    lookup_source if lookup_source is not None else "skip",
                )
                skip_item = RenameItem(
                    old_path=group.video_path,
                    new_path="(AniDB lookup failed)",
                    kind=RenameKind.SKIP,
                    anime_type="",
                )
                all_items.append(([skip_item], group.video_path))
                continue
            assert lookup_source is not None
            _LOG.info(
                "phase=lookup basename=%s fid=%d lookup_source=%s",
                basename,
                info.fid,
                lookup_source,
            )
            use_folder = args.folder or args.plex
            folder_tpl: str | None = None
            if use_folder:
                folder_tpl = args.folder_template
                if args.plex:
                    folder_tpl = _apply_plex_suffix(folder_tpl)
            items = build_plan(group, info, args.template, args.dest, folder_template=folder_tpl)
            all_items.append((items, group.video_path))
            resolved_infos.append(info)
            overlay.settle_lookup(cached_count, fetched_count, no_match_count, _link_state(client))

    ledger.hash_lookup(cached=cached_count, fetched=fetched_count, no_match=no_match_count)

    flat_items, folder_conflicts = flatten_and_validate_folder_renames(all_items)
    if not flat_items:
        console.print("[yellow]No video files found.[/yellow]")
        ledger.footer(RunOutcome.NO_MATCHES)
        sys.exit(RunOutcome.NO_MATCHES.exit_code)

    resolution = resolve_destination_conflicts(
        flat_items, policy=args.on_conflict, strategy=args.name_dedupe
    )
    flat_items = resolution.plan
    conflict_indexes = resolution.conflict_indexes
    _LOG.info(
        "phase=plan item_count=%d dry_run=%s preview_format=%s",
        len(flat_items),
        args.dry_run,
        args.preview_format,
    )
    # The plan block replaces the Warnings panel and the Rich-Table preview:
    # folder conflicts render as inline warning lines, destination conflicts as
    # flagged plan lines (SPEC §3). json mode keeps its plan dump unchanged.
    ledger.plan(
        flat_items,
        conflict_indexes=conflict_indexes,
        folder_conflicts=folder_conflicts,
        dry_run=args.dry_run,
        render_block=args.preview_format != "json",
    )
    if args.preview_format == "json":
        preview_items = [
            {
                "old_path": item.old_path,
                "new_path": item.new_path,
                "kind": item.kind.value,
                "anime_type": item.anime_type,
            }
            for item in flat_items
        ]
        console.print_json(json.dumps(preview_items))

    if resolution.should_fail:
        # Post-plan fatal abort: degraded to the verdict line only (SPEC §3).
        ledger.verdict_only(RunOutcome.CONFLICT_FAIL_ABORT)
        sys.exit(RunOutcome.CONFLICT_FAIL_ABORT.exit_code)

    file_items_count = sum(1 for i in flat_items if i.kind == RenameKind.FILE)
    if file_items_count == 0:
        console.print("[yellow]No renames to apply (AniDB lookup failed for all files).[/yellow]")
        ledger.footer(RunOutcome.NO_MATCHES)
        sys.exit(RunOutcome.NO_MATCHES.exit_code)

    if args.dry_run:
        _LOG.info("phase=apply dry_run=yes file_operations=%d", file_items_count)
        plan_skips = sum(1 for i in flat_items if i.kind == RenameKind.SKIP)
        dry_run_partial = plan_skips > 0 or bool(conflict_indexes)
        exit_code = EXIT_PARTIAL if dry_run_partial else 0
        final_exit = _run_mylist_if_requested(
            args=args,
            client=client,
            console=console,
            ledger=ledger,
            resolved_infos=resolved_infos,
            exit_code=exit_code,
        )
        if final_exit == 0:
            outcome = RunOutcome.DRY_RUN_CLEAN
        elif dry_run_partial:
            outcome = RunOutcome.DRY_RUN_CONFLICTS
        else:
            outcome = RunOutcome.MYLIST_FAILED
        ledger.footer(outcome)
        sys.exit(final_exit)

    do_apply = args.yes
    if not do_apply:
        reply = _prompt_confirmation("Apply these renames?")
        do_apply = reply in ("y", "a")

    if not do_apply:
        ledger.declined()
        final_exit = _run_mylist_if_requested(
            args=args,
            client=client,
            console=console,
            ledger=ledger,
            resolved_infos=resolved_infos,
            exit_code=0,
        )
        outcome = RunOutcome.DECLINED if final_exit == 0 else RunOutcome.MYLIST_FAILED
        ledger.footer(outcome)
        sys.exit(final_exit)

    file_items = [i for i in flat_items if i.kind == RenameKind.FILE]
    _LOG.info("phase=apply dry_run=no file_operations=%d", len(file_items))
    # Silent during apply (SPEC §3): no transient Live/Progress on the ledger
    # path; the settled apply counter row is the phase's one permanent line.
    result = apply_plan(flat_items, db_path, dry_run=False)
    for failure in result.failures:
        review_suffix = (
            " — destination exists; manual review required" if failure.dst_exists_after else ""
        )
        console.print(
            f"[red]Apply failed: {rich_escape(failure.src)} -> {rich_escape(failure.dst)} "
            f"({rich_escape(failure.reason)}){review_suffix}[/red]",
            soft_wrap=True,
            highlight=False,
        )
    ledger.apply(
        renamed=result.applied,
        dest_exists=result.skipped_destination_exists,
        source_missing=result.skipped_source_missing,
        apply_failed=result.skipped_apply_failed,
    )
    # Exit 2 when there were skips (plan skips or apply skips) per spec §6
    plan_skips = sum(1 for i in flat_items if i.kind == RenameKind.SKIP)
    had_skips = plan_skips > 0 or result.skipped_total > 0
    exit_code = EXIT_PARTIAL if had_skips else 0
    # MyList runs before the footer so a mylist row and the recap are truthful.
    final_exit = _run_mylist_if_requested(
        args=args,
        client=client,
        console=console,
        ledger=ledger,
        resolved_infos=resolved_infos,
        exit_code=exit_code,
    )
    ledger.footer(_applied_outcome(final_exit, had_skips=had_skips))
    sys.exit(final_exit)


def _applied_outcome(final_exit: int, *, had_skips: bool) -> RunOutcome:
    """Map an applied run's final exit code to its footer verdict (SPEC §4).

    exit ``0`` → applied clean; exit ``2`` with skips → completed with skips;
    exit ``2`` with no skips → the MyList-failure lift (renames applied, some
    MyList updates failed).
    """
    if final_exit == 0:
        return RunOutcome.APPLIED_CLEAN
    if had_skips:
        return RunOutcome.APPLIED_WITH_SKIPS
    return RunOutcome.MYLIST_FAILED


def _run_mylist_if_requested(
    *,
    args: argparse.Namespace,
    client: Any,
    console: Console,
    ledger: Ledger,
    resolved_infos: list[FileInfo],
    exit_code: int,
) -> int:
    """Run MyList wizard when requested and fold failures into exit code semantics."""
    if not args.mylist:
        return exit_code
    mylist_result = run_mylist_wizard(
        console=console,
        client=client,
        file_infos=resolved_infos,
        confirm=_prompt_yes_no,
    )
    ledger.mylist(mylist_result.applied)
    mylist_incomplete = mylist_result.failed > 0 or mylist_result.banned
    if mylist_result.attempted and mylist_incomplete and exit_code == 0:
        return EXIT_PARTIAL
    return exit_code


if __name__ == "__main__":
    main()
