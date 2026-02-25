"""CLI: discover, hash, lookup, plan, preview, apply (dry-run / Y/n). Rich UI: progress bars, headings, Warnings panel."""

from __future__ import annotations

import argparse
import signal
import sys
from typing import Any

from dotenv import load_dotenv
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
    clear_file_anidb_cache,
    clear_file_anidb_entries,
    get_db_path,
    init_db,
    get_file_info,
    set_file_info,
)
from anipyrenamer.discovery import discover, get_file_size
from anipyrenamer.ed2k import compute_ed2k
from anipyrenamer.models import RenameItem, RenameKind
from anipyrenamer.naming import DEFAULT_FILE_TEMPLATE, DEFAULT_FOLDER_TEMPLATE
from anipyrenamer.plan import build_plan
from anipyrenamer.validation import (
    detect_destination_conflicts,
    flatten_and_validate_folder_renames,
)

load_dotenv()

# Exit code when user interrupts (e.g. Ctrl+C)
EXIT_INTERRUPTED = 130
# Exit code when completed with partial failures/skips/conflicts (spec Part B)
EXIT_PARTIAL = 2

PLEX_SUFFIX = " [anidb-%aid%]"


def _apply_plex_suffix(template: str) -> str:
    """Insert the Plex/ASS/HAMA AniDB-id tag before %ext% (or append if no %ext%)."""
    if "%ext%" in template:
        return template.replace("%ext%", f"{PLEX_SUFFIX}%ext%", 1)
    return template + PLEX_SUFFIX


def _disconnect_anidb(client: Any, console: Console, had_session: bool) -> None:
    """Log out from AniDB and print disconnect message only when we had a session."""
    if client is not None:
        client.logout()
    if had_session:
        console.print("[cyan]✓ Disconnected from AniDB.[/cyan]")


def main() -> None:
    """Run full pipeline: discover, hash, lookup, plan, preview, apply."""
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
        help="Auto-accept apply and batch continuation.",
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
        "--batch-size",
        type=int,
        default=30,
        help="Items per 'Continue with next N?' (default: 30).",
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
    args = parser.parse_args()

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
    if not groups:
        console.print("[yellow]No video files found.[/yellow]")
        sys.exit(0)

    if args.clear_cache:
        entries: list[tuple[int, str]] = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Clearing cache for scanned files…", total=len(groups))
            for group in groups:
                size = get_file_size(group.video_path)
                ed2k = compute_ed2k(group.video_path)
                entries.append((size, ed2k))
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
        _run_after_anidb_ready(args, client, had_session, console, db_path, groups)
    except KeyboardInterrupt:
        sys.exit(EXIT_INTERRUPTED)


def _run_after_anidb_ready(
    args: argparse.Namespace,
    client: Any,
    had_session: bool,
    console: Console,
    db_path: str,
    groups: list[Any],
) -> None:
    """Run hashing, lookup, plan, preview, apply. Guarantees AniDB logout in finally."""
    try:
        _do_hashing_lookup_plan_apply(args, client, console, db_path, groups)
    finally:
        _disconnect_anidb(client, console, had_session)


def _do_hashing_lookup_plan_apply(
    args: argparse.Namespace,
    client: Any,
    console: Console,
    db_path: str,
    groups: list[Any],
) -> None:
    """Hashing and lookup, then plan, preview, and optionally apply."""
    console.print("[bold]Hashing and lookup[/bold]")
    all_items: list[tuple[list[RenameItem], str]] = []  # (items, batch_id placeholder)
    # Overall: bar only (no ETA at top). Per-file: single live line (path + bar + MB + speed + ETA).
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
    file_task = progress_file.add_task("", total=1, visible=False)
    group_render = Group(progress_overall, progress_file)

    with Live(group_render, console=console, refresh_per_second=8) as live:
        for i, group in enumerate(groups):
            path_str = str(group.video_path)
            size = get_file_size(group.video_path)
            progress_file.update(
                file_task,
                description=f"[yellow]{rich_escape(path_str)}[/yellow]",
                total=max(1, size),
                completed=0,
                visible=True,
            )

            def _on_progress(br: int, tot: int) -> None:
                progress_file.update(file_task, completed=br, total=max(1, tot))
                live.refresh()

            ed2k = compute_ed2k(group.video_path, progress_callback=_on_progress)
            info = get_file_info(db_path, size, ed2k)
            if info is not None and args.debug:
                console.print(
                    f"[dim][debug] Using cached AniDB data for size={size} ed2k={ed2k[:16]}…[/dim]"
                )
            if info is not None and client:
                from anipyrenamer.anidb import _looks_like_hash

                if _looks_like_hash(info.anime_title):
                    info = None
                    if args.debug:
                        console.print(
                            "[dim][debug] Cached title looks like hash; refetching from AniDB.[/dim]"
                        )
            if info is not None:
                console.print(
                    f"[blue]📁 Using local cache for {rich_escape(path_str)}[/blue] "
                    "(use [bold]--clear-cache[/bold] to refetch from AniDB)"
                )
            if info is None and client:
                info = client.file_lookup(size, ed2k)
                if info is None and not client.has_session:
                    client.login()
                    info = client.file_lookup(size, ed2k)
                if info is not None:
                    set_file_info(db_path, info)
                    console.print(
                        f"[green]🌐 Fetched from AniDB for {rich_escape(path_str)}[/green]"
                    )
            if info is None:
                # Show in plan table so user sees the file and why it wasn't renamed
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
            use_folder = args.folder or args.plex
            folder_tpl: str | None = None
            if use_folder:
                folder_tpl = args.folder_template
                if args.plex:
                    folder_tpl = _apply_plex_suffix(folder_tpl)
            items = build_plan(group, info, args.template, args.dest, folder_template=folder_tpl)
            all_items.append((items, group.video_path))
            progress_overall.advance(overall_task)
            progress_overall.update(
                overall_task,
                description=f"Hashing and lookup {i + 1}/{len(groups)}",
            )

    flat_items, folder_conflicts = flatten_and_validate_folder_renames(all_items)
    if not flat_items:
        console.print("[yellow]No video files found.[/yellow]")
        sys.exit(0)

    dest_conflicts = detect_destination_conflicts(flat_items)
    warnings: list[str] = []
    for msg in folder_conflicts:
        warnings.append(f"[yellow]{msg}[/yellow]")
    for msg in dest_conflicts:
        warnings.append(f"[dim]{msg}[/dim]")
    if warnings:
        console.print(Panel("\n".join(warnings), title="Warnings", border_style="yellow"))

    console.print("[bold]Rename plan[/bold]")
    preview_plan(flat_items, console=console)

    file_items_count = sum(1 for i in flat_items if i.kind == RenameKind.FILE)
    if file_items_count == 0:
        console.print("[yellow]No renames to apply (AniDB lookup failed for all files).[/yellow]")
        sys.exit(0)

    if args.dry_run:
        console.print("[green]Dry run; no files changed.[/green]")
        plan_skips = sum(1 for i in flat_items if i.kind == RenameKind.SKIP)
        sys.exit(EXIT_PARTIAL if plan_skips > 0 else 0)

    do_apply = args.yes
    if not do_apply:
        try:
            reply = input("Apply these renames? [y/N] ").strip().lower()
            do_apply = reply in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            do_apply = False

    if not do_apply:
        console.print("[red]Aborted.[/red]")
        sys.exit(0)

    console.print("[bold]Apply[/bold]")
    file_items = [i for i in flat_items if i.kind == RenameKind.FILE]
    total_apply = len(file_items)
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
    # Exit 2 when there were skips (plan skips or apply skips) per spec Part B
    plan_skips = sum(1 for i in flat_items if i.kind == RenameKind.SKIP)
    if plan_skips > 0 or skipped_count > 0:
        sys.exit(EXIT_PARTIAL)
    sys.exit(0)


if __name__ == "__main__":
    main()
