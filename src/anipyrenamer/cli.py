"""CLI: discover, hash, lookup, plan, preview, apply (dry-run / Y/n). Rich UI: progress bars, headings, Warnings panel."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any, Callable

from dotenv import find_dotenv, load_dotenv
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.markup import escape as rich_escape
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from anipyrenamer.apply import apply_plan, preview_plan
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
from anipyrenamer.ed2k import compute_ed2k
from anipyrenamer.models import DiscoveredGroup, FileInfo, RenameItem, RenameKind
from anipyrenamer.mylist import run_mylist_wizard
from anipyrenamer.naming import DEFAULT_FILE_TEMPLATE, DEFAULT_FOLDER_TEMPLATE
from anipyrenamer.plan import build_plan
from anipyrenamer.permissions import warn_if_world_readable
from anipyrenamer.validation import flatten_and_validate_folder_renames


def _get_well_known_env_path() -> Path | None:
    """Path to .env in a well-known config dir for global installs (Windows: %%APPDATA%%/anipyrenamer, Unix: ~/.config/anipyrenamer)."""
    if os.name == "nt":
        apd = os.environ.get("APPDATA")
        if not apd:
            return None
        return Path(apd) / "anipyrenamer" / ".env"
    return Path.home() / ".config" / "anipyrenamer" / ".env"


def _load_env() -> None:
    """Load ``.env``: walk **upward from process cwd**, then well-known config path.

    Uses ``find_dotenv(usecwd=True)`` so discovery matches operator expectations when
    running from an arbitrary folder (editable installs no longer implicitly pick up a
    dev-repo ``.env`` via ``python-dotenv``'s default anchor). Loads do not override
    already-set environment variables (override=False).
    """
    dotted_usecwd = find_dotenv(usecwd=True) or ""

    if dotted_usecwd:
        load_dotenv(dotted_usecwd)
        # Warn against the *resolved* discovered path (may be a parent .env),
        # not a literal cwd .env, which may be a different or non-existent file.
        discovered = Path(dotted_usecwd)
        if discovered.exists():
            warn_if_world_readable(discovered)
    well_known = _get_well_known_env_path()
    if well_known is not None:
        if well_known.exists():
            warn_if_world_readable(well_known)
        load_dotenv(well_known)


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


def main() -> None:
    """Run full pipeline: discover, hash, lookup, plan, preview, apply."""
    _load_env()
    parser = argparse.ArgumentParser(
        prog="anipyrenamer",
        description="Rename anime files using ED2K hash and AniDB.",
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

    _configure_cli_logging(level_name=args.log_level, log_file=args.log_file)

    if not args.paths:
        parser.print_help()
        sys.exit(0)

    console = Console()
    db_path = get_db_path(args.db)
    init_db(db_path)
    if args.clear_cache_all:
        clear_file_anidb_cache(db_path)
        console.print("[dim]Entire AniDB file cache cleared.[/dim]")

    console.print("[bold]Discovery[/bold]")
    groups = discover(args.paths)
    _LOG.info("phase=discovery group_count=%d", len(groups))
    if not groups:
        console.print("[yellow]No video files found.[/yellow]")
        sys.exit(0)

    precomputed_hashes: dict[str, tuple[int, str]] = {}
    if args.clear_cache:
        entries: list[tuple[int, str]] = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Clearing cache for scanned files…", total=len(groups))
            for group in groups:
                _, size, ed2k = _hash_group(group)
                entries.append((size, ed2k))
                precomputed_hashes[group.video_path] = (size, ed2k)
                progress.advance(task)
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
            args, client, had_session, console, db_path, groups, precomputed_hashes
        )
    except KeyboardInterrupt:
        sys.exit(EXIT_INTERRUPTED)


def _run_after_anidb_ready(
    args: argparse.Namespace,
    client: Any,
    had_session: bool,
    console: Console,
    db_path: str,
    groups: list[Any],
    precomputed_hashes: dict[str, tuple[int, str]],
) -> None:
    """Run hashing, lookup, plan, preview, apply. Guarantees AniDB logout in finally."""
    try:
        _do_hashing_lookup_plan_apply(args, client, console, db_path, groups, precomputed_hashes)
    finally:
        _disconnect_anidb(client, console, had_session)


def _do_hashing_lookup_plan_apply(
    args: argparse.Namespace,
    client: Any,
    console: Console,
    db_path: str,
    groups: list[Any],
    precomputed_hashes: dict[str, tuple[int, str]],
) -> None:
    """Hashing and lookup, then plan, preview, and optionally apply."""
    console.print("[bold]Hashing and lookup[/bold]")
    _LOG.info("phase=hash_lookup group_count=%d", len(groups))
    all_items: list[tuple[list[RenameItem], str]] = []  # (items, batch_id placeholder)
    resolved_infos: list[FileInfo] = []
    # Overall: bar only (no ETA at top). Per-file: one row during hashing (current file).
    progress_overall = Progress(
        SpinnerColumn(),
        BarColumn(bar_width=24, style="blue", complete_style="green"),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    )
    progress_file = Progress(
        SpinnerColumn(),
        BarColumn(bar_width=28, style="bright_magenta", complete_style="green"),
        TextColumn("[progress.description]{task.description}"),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(compact=True),
        console=console,
    )
    overall_task = progress_overall.add_task(
        f"Hashing and lookup 0/{len(groups)}",
        total=len(groups),
    )
    group_render = Group(progress_overall, progress_file)

    max_consecutive_timeouts = 3
    consecutive_timeouts = 0
    anidb_aborted = False

    with Live(group_render, console=console, refresh_per_second=8) as live:
        for i, group in enumerate(groups):
            path_str = str(group.video_path)
            basename = Path(path_str).name
            lookup_source: str | None = None
            cached_hash = precomputed_hashes.get(group.video_path)
            if cached_hash is not None:
                size, ed2k = cached_hash
            else:
                size = get_file_size(group.video_path)
                file_task = progress_file.add_task(
                    f"[yellow]{rich_escape(Path(group.video_path).name)}[/yellow]",
                    total=max(1, size),
                    completed=0,
                )

                def _on_progress(br: int, tot: int) -> None:
                    progress_file.update(file_task, completed=br, total=max(1, tot))
                    live.refresh()

                ed2k = compute_ed2k(group.video_path, progress_callback=_on_progress)
                progress_file.remove_task(file_task)
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
                console.print(
                    f"[blue]📁 Using local cache for {rich_escape(path_str)}[/blue] "
                    "(use [bold]--clear-cache[/bold] to refetch from AniDB)"
                )
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
                    console.print(
                        f"[green]🌐 Fetched from AniDB for {rich_escape(path_str)}[/green]"
                    )
            if info is None:
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
                progress_overall.advance(overall_task)
                progress_overall.update(
                    overall_task,
                    description=f"Hashing and lookup {i + 1}/{len(groups)}",
                )
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
            progress_overall.advance(overall_task)
            progress_overall.update(
                overall_task,
                description=f"Hashing and lookup {i + 1}/{len(groups)}",
            )

    flat_items, folder_conflicts = flatten_and_validate_folder_renames(all_items)
    if not flat_items:
        console.print("[yellow]No video files found.[/yellow]")
        sys.exit(0)

    resolution = resolve_destination_conflicts(
        flat_items, policy=args.on_conflict, strategy=args.name_dedupe
    )
    flat_items = resolution.plan
    dest_conflicts = resolution.warnings
    conflict_indexes = resolution.conflict_indexes
    warnings: list[str] = []
    for msg in folder_conflicts:
        warnings.append(f"[yellow]{msg}[/yellow]")
    for msg in dest_conflicts:
        warnings.append(f"[dim]{msg}[/dim]")
    if warnings:
        console.print(Panel("\n".join(warnings), title="Warnings", border_style="yellow"))

    console.print("[bold]Rename plan[/bold]")
    _LOG.info(
        "phase=plan item_count=%d dry_run=%s preview_format=%s",
        len(flat_items),
        args.dry_run,
        args.preview_format,
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
    else:
        preview_plan(flat_items, console=console)

    if resolution.should_fail:
        console.print("[red]Aborted due to destination conflicts (--on-conflict=fail).[/red]")
        sys.exit(1)

    file_items_count = sum(1 for i in flat_items if i.kind == RenameKind.FILE)
    if file_items_count == 0:
        console.print("[yellow]No renames to apply (AniDB lookup failed for all files).[/yellow]")
        sys.exit(0)

    if args.dry_run:
        _LOG.info("phase=apply dry_run=yes file_operations=%d", file_items_count)
        console.print("[green]Dry run; no files changed.[/green]")
        plan_skips = sum(1 for i in flat_items if i.kind == RenameKind.SKIP)
        exit_code = EXIT_PARTIAL if plan_skips > 0 or conflict_indexes else 0
        sys.exit(
            _run_mylist_if_requested(
                args=args,
                client=client,
                console=console,
                resolved_infos=resolved_infos,
                exit_code=exit_code,
            )
        )

    do_apply = args.yes
    if not do_apply:
        reply = _prompt_confirmation("Apply these renames?")
        do_apply = reply in ("y", "a")

    if not do_apply:
        console.print("[red]Aborted.[/red]")
        sys.exit(
            _run_mylist_if_requested(
                args=args,
                client=client,
                console=console,
                resolved_infos=resolved_infos,
                exit_code=0,
            )
        )

    console.print("[bold]Apply[/bold]")
    file_items = [i for i in flat_items if i.kind == RenameKind.FILE]
    total_apply = len(file_items)
    _LOG.info("phase=apply dry_run=no file_operations=%d", total_apply)
    # One overall bar (Renaming N/M) only; no per-file line.
    progress_apply_overall = Progress(
        SpinnerColumn(),
        BarColumn(bar_width=24, style="blue", complete_style="green"),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    )
    overall_apply_task = progress_apply_overall.add_task(
        f"Renaming 0/{total_apply}",
        total=total_apply,
    )

    def apply_progress(idx: int, total: int, item: RenameItem, skipped: bool | None) -> None:
        if skipped is None:
            progress_apply_overall.update(
                overall_apply_task,
                description=f"Renaming {idx - 1}/{total}",
            )
            if live_apply_ref[0] is not None:
                live_apply_ref[0].refresh()
        else:
            progress_apply_overall.advance(overall_apply_task)
            progress_apply_overall.update(
                overall_apply_task,
                description=f"Renaming {idx}/{total}",
            )
            if live_apply_ref[0] is not None:
                live_apply_ref[0].refresh()

    live_apply_ref: list[Live | None] = [None]
    apply_group = Group(progress_apply_overall)
    with Live(apply_group, console=console, refresh_per_second=8) as live_apply:
        live_apply_ref[0] = live_apply
        applied_count, skipped_count = apply_plan(
            flat_items,
            db_path,
            dry_run=False,
            progress_callback=apply_progress,
        )
    console.print("[green]Renames applied.[/green]")
    # Exit 2 when there were skips (plan skips or apply skips) per spec §6
    plan_skips = sum(1 for i in flat_items if i.kind == RenameKind.SKIP)
    exit_code = 0
    if plan_skips > 0 or skipped_count > 0:
        exit_code = EXIT_PARTIAL
    sys.exit(
        _run_mylist_if_requested(
            args=args,
            client=client,
            console=console,
            resolved_infos=resolved_infos,
            exit_code=exit_code,
        )
    )


def _run_mylist_if_requested(
    *,
    args: argparse.Namespace,
    client: Any,
    console: Console,
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
    if mylist_result.attempted and mylist_result.failed > 0 and exit_code == 0:
        return EXIT_PARTIAL
    return exit_code


if __name__ == "__main__":
    main()
