"""CLI: discover, hash, lookup, plan, preview, apply (dry-run / Y/n)."""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

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
from anipyrenamer.models import RenameItem
from anipyrenamer.naming import DEFAULT_FILE_TEMPLATE, DEFAULT_FOLDER_TEMPLATE
from anipyrenamer.plan import build_plan
from anipyrenamer.validation import (
    detect_destination_conflicts,
    flatten_and_validate_folder_renames,
)

load_dotenv()


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
    client = None
    if not args.offline:
        from anipyrenamer.anidb import AniDBConfig, AniDBClient

        cfg = AniDBConfig.from_env()
        if cfg.username and cfg.password:
            client = AniDBClient(cfg, debug=args.debug)
            try:
                ok, msg = client.login()
                if not ok:
                    if msg and "555" in msg and "BANNED" in msg.upper():
                        console.print(
                            "[red]AniDB returned: account or IP is temporarily banned.[/red] "
                            "Wait before retrying; use [bold]--offline[/bold] to use cache only."
                        )
                    else:
                        console.print("[red]AniDB login failed.[/red]")
                    client.logout()
                    sys.exit(1)
            except TimeoutError:
                console.print(
                    "[red]AniDB connection timed out.[/red] "
                    "You may be rate-limited or temporarily banned. "
                    "Try again later or use [bold]--offline[/bold] to use cache only."
                )
                client.logout()
                sys.exit(1)
        else:
            console.print(
                "[yellow]ANIDB_USERNAME/ANIDB_PASSWORD not set; using cache only.[/yellow]"
            )

    all_items: list[tuple[list[RenameItem], str]] = []  # (items, batch_id placeholder)
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Hashing and lookup…", total=len(groups))
        for group in groups:
            progress.update(task, description=f"Processing {group.video_path}…")
            size = get_file_size(group.video_path)
            ed2k = compute_ed2k(group.video_path)
            info = get_file_info(db_path, size, ed2k)
            if info is not None and args.debug:
                console.print(f"[dim][debug] Using cached AniDB data for size={size} ed2k={ed2k[:16]}…[/dim]")
            # Refetch if cached title looks like a hash (old parser bug)
            if info is not None and client:
                from anipyrenamer.anidb import _looks_like_hash
                if _looks_like_hash(info.anime_title):
                    info = None
                    if args.debug:
                        console.print("[dim][debug] Cached title looks like hash; refetching from AniDB.[/dim]")
            if info is None and client:
                info = client.file_lookup(size, ed2k)
                if info is None and client._session is None:
                    client.login()
                    info = client.file_lookup(size, ed2k)
                if info is not None:
                    set_file_info(db_path, info)
            if info is None:
                progress.console.print(
                    f"[dim]No AniDB data for {group.video_path}, skipping.[/dim]"
                )
                progress.advance(task)
                continue
            folder_tpl = args.folder_template if args.folder else None
            items = build_plan(group, info, args.template, args.dest, folder_template=folder_tpl)
            all_items.append((items, group.video_path))
            progress.advance(task)

    if client:
        client.logout()

    if not all_items:
        console.print("[yellow]No renames to apply.[/yellow]")
        sys.exit(0)

    flat_items, folder_conflicts = flatten_and_validate_folder_renames(all_items)
    for msg in folder_conflicts:
        console.print(f"[yellow]{msg}[/yellow]")

    dest_conflicts = detect_destination_conflicts(flat_items)
    for msg in dest_conflicts:
        console.print(f"[dim]{msg}[/dim]")

    preview_plan(flat_items, console=console)

    if args.dry_run:
        console.print("[green]Dry run; no files changed.[/green]")
        sys.exit(0)

    do_apply = args.yes
    if not do_apply:
        try:
            reply = input("Apply these renames? [y/N] ").strip().lower()
            do_apply = reply in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            do_apply = False

    if not do_apply:
        console.print("Aborted.")
        sys.exit(0)

    apply_plan(flat_items, db_path, dry_run=False)
    console.print("[green]Renames applied.[/green]")
    sys.exit(0)


if __name__ == "__main__":
    main()
