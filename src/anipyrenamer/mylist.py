"""Phase 2 MyList interactive wizard."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import Callable, Literal, cast

from rich.console import Console, Group
from rich.live import Live
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn

from anipyrenamer.models import FileInfo


ConfirmFn = Callable[[str], str]
ItemProgressPhase = Literal["start", "end"]
ItemProgressFn = Callable[[int, int, ItemProgressPhase], None]

MYLIST_STORAGE_CHOICES: dict[str, tuple[int, str]] = {
    "1": (0, "Unknown"),
    "2": (1, "Internal HDD"),
    "3": (2, "External CD/DVD"),
    "4": (3, "Deleted"),
    "5": (4, "Remote"),
}


@dataclass
class MyListRunResult:
    """Outcome of applying MyList changes."""

    applied: int = 0
    failed: int = 0
    skipped: int = 0
    selected_files: int = 0
    attempted: bool = False


def _select_storage(console: Console) -> tuple[int | None, str | None, bool]:
    """Prompt for storage selection. Returns (state, label, aborted)."""
    console.print("Choose storage:")
    console.print("  1) Unknown")
    console.print("  2) Internal HDD")
    console.print("  3) External CD/DVD")
    console.print("  4) Deleted")
    console.print("  5) Remote")
    console.print("  6) Exit")

    while True:
        raw = input("Storage option (1-6): ").strip()
        if raw == "6":
            return (None, None, True)
        choice = MYLIST_STORAGE_CHOICES.get(raw)
        if choice is not None:
            state, label = choice
            return (state, label, False)
        console.print("[yellow]Invalid option. Enter 1-6.[/yellow]")


def run_mylist_wizard(
    *,
    console: Console,
    client: object | None,
    file_infos: list[FileInfo],
    confirm: ConfirmFn,
    item_progress: ItemProgressFn | None = None,
) -> MyListRunResult:
    """Run interactive MyList wizard and apply requested updates."""
    result = MyListRunResult()

    unique_infos = [info for info in file_infos if info.fid > 0]
    unique_by_fid: dict[int, FileInfo] = {info.fid: info for info in unique_infos}
    entries = list(unique_by_fid.values())
    if not entries:
        console.print("[yellow]MyList skipped: no AniDB file ids (fid) available.[/yellow]")
        result.skipped = 1
        return result
    result.selected_files = len(entries)

    update_reply = confirm("Would you like to update MyList?")
    if update_reply == "n":
        console.print("[dim]MyList update skipped by user.[/dim]")
        return result
    auto_yes_remaining = update_reply == "a"

    if client is None:
        console.print("[yellow]MyList skipped: AniDB session is not available.[/yellow]")
        result.skipped = len(entries)
        return result

    set_storage_reply = "y" if auto_yes_remaining else confirm("Would you like to set storage?")
    auto_yes_remaining = auto_yes_remaining or set_storage_reply == "a"
    storage_state: int | None = None
    storage_label: str | None = None
    if set_storage_reply in ("y", "a"):
        storage_state, storage_label, aborted = _select_storage(console)
        if aborted:
            console.print("[yellow]MyList wizard aborted at storage step.[/yellow]")
            return result

    add_reply = "y" if auto_yes_remaining else confirm("Would you like to add file(s) to MyList?")
    auto_yes_remaining = auto_yes_remaining or add_reply == "a"
    add_to_mylist = add_reply in ("y", "a")

    watched_reply = "y" if auto_yes_remaining else confirm("Would you like to mark as watched?")
    auto_yes_remaining = auto_yes_remaining or watched_reply == "a"
    mark_watched = watched_reply in ("y", "a")

    console.print("[bold]MyList summary[/bold]")
    console.print(f"- Files with AniDB fid: {len(entries)}")
    console.print(f"- Add to MyList: {'yes' if add_to_mylist else 'no'}")
    console.print(f"- Mark watched: {'yes' if mark_watched else 'no'}")
    console.print(f"- Storage: {storage_label if storage_label else 'unchanged'}")

    apply_reply = "y" if auto_yes_remaining else confirm("Apply MyList updates?")
    if apply_reply == "n":
        console.print("[dim]MyList changes not applied.[/dim]")
        return result

    result.attempted = True
    total = len(entries)
    use_terminal_progress = console.is_terminal
    progress_mylist: Progress | None = None
    overall_task: TaskID | None = None
    live_ref: list[Live | None] = [None]

    if use_terminal_progress:
        progress_mylist = Progress(
            SpinnerColumn(),
            BarColumn(bar_width=24, style="blue", complete_style="green"),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        )
        overall_task = progress_mylist.add_task(
            f"MyList Updating 0/{total}",
            total=total,
        )

    outer_live: Live | nullcontext[Live | None]
    if use_terminal_progress and progress_mylist is not None:
        outer_live = Live(Group(progress_mylist), console=console, refresh_per_second=8)
    else:
        # Mypy narrows `nullcontext[Live | None](None)` to `nullcontext[None]` without cast.
        outer_live = cast(
            nullcontext[Live | None],
            nullcontext[Live | None](None),
        )

    def _refresh_live() -> None:
        live = live_ref[0]
        if live is not None:
            live.refresh()

    with outer_live as live:
        if live is not None:
            live_ref[0] = live
        for idx, info in enumerate(entries, start=1):
            if item_progress is not None:
                item_progress(idx, total, "start")
            if progress_mylist is not None and overall_task is not None:
                progress_mylist.update(
                    overall_task,
                    description=f"MyList Updating {idx - 1}/{total}",
                )
                _refresh_live()
            ok, msg = client.mylist_add_or_update_by_fid(  # type: ignore[attr-defined]
                info.fid,
                add_to_mylist=add_to_mylist,
                state=storage_state,
                storage=storage_label,
                viewed=mark_watched,
            )
            if ok:
                result.applied += 1
            else:
                result.failed += 1
                console.print(f"[yellow]MyList update failed for fid={info.fid}: {msg}[/yellow]")
            if progress_mylist is not None and overall_task is not None:
                progress_mylist.advance(overall_task)
                progress_mylist.update(
                    overall_task,
                    description=f"MyList Updating {idx}/{total}",
                )
                _refresh_live()
            if item_progress is not None:
                item_progress(idx, total, "end")

    console.print("Applied.")
    return result
