"""Phase 2 MyList interactive wizard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rich.console import Console

from anipyrenamer.models import FileInfo


ConfirmFn = Callable[[str], str]

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
    for info in entries:
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

    console.print("Applied.")
    return result
