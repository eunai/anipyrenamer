"""Optional advanced MyList interactive wizard."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import Callable, Literal, cast

from rich.console import Console, Group
from rich.live import Live
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn

from anipyrenamer.anidb import AniDBBannedError
from anipyrenamer.models import FileInfo


ConfirmFn = Callable[[str], str]
ItemProgressPhase = Literal["start", "end"]
ItemProgressFn = Callable[[int, int, ItemProgressPhase], None]

# 0-indexed storage menu: the typed key equals the AniDB MyList state code (R4).
# Labels are cosmetic and decoupled from the state code. The menu lines and the
# Exit key in `_select_storage` are rendered from this single dict so the
# displayed numbers can never drift from the state codes.
MYLIST_STORAGE_CHOICES: dict[str, tuple[int, str]] = {
    "0": (0, "Unknown/None"),
    "1": (1, "Internal"),
    "2": (2, "External"),
    "3": (3, "Deleted"),
    "4": (4, "Remote"),
}


@dataclass
class MyListRunResult:
    """Outcome of applying MyList changes."""

    applied: int = 0
    failed: int = 0
    skipped: int = 0
    selected_files: int = 0
    attempted: bool = False
    banned: bool = False
    ban_reason: str = ""


def _select_storage(console: Console) -> tuple[int | None, str | None, bool]:
    """Prompt for storage selection. Returns (state, label, aborted).

    The menu is 0-indexed and the typed key equals the AniDB MyList state code.
    Menu lines and the Exit key are rendered from ``MYLIST_STORAGE_CHOICES`` so
    the displayed numbers can never drift from the state codes.
    """
    states = [state for state, _label in MYLIST_STORAGE_CHOICES.values()]
    lowest_key = str(min(states))
    exit_key = str(max(states) + 1)

    console.print("Choose storage:")
    for key, (_state, label) in MYLIST_STORAGE_CHOICES.items():
        console.print(f"  {key}) {label}")
    console.print(f"  {exit_key}) Exit")

    while True:
        raw = input(f"Storage option ({lowest_key}-{exit_key}): ").strip()
        if raw == exit_key:
            return (None, None, True)
        choice = MYLIST_STORAGE_CHOICES.get(raw)
        if choice is not None:
            state, label = choice
            return (state, label, False)
        console.print(f"[yellow]Invalid option. Enter {lowest_key}-{exit_key}.[/yellow]")


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

    # R6: fail fast. With no AniDB session there is nothing to apply, so skip
    # immediately — before any prompt.
    if client is None:
        console.print("[yellow]MyList skipped: AniDB session is not available.[/yellow]")
        result.skipped = len(entries)
        return result

    # R2: every prompt is an independent yes/no — no answer auto-confirms a later
    # prompt. Only an explicit "y" is affirmative.
    storage_state: int | None = None
    storage_label: str | None = None
    if confirm("Would you like to set storage?") == "y":
        storage_state, storage_label, aborted = _select_storage(console)
        if aborted:
            console.print("[yellow]MyList wizard aborted at storage step.[/yellow]")
            return result

    add_to_mylist = confirm("Would you like to add file(s) to MyList?") == "y"
    mark_watched = confirm("Would you like to mark as watched?") == "y"

    console.print("[bold]MyList summary[/bold]")
    console.print(f"- Files with AniDB fid: {len(entries)}")
    console.print(f"- Add to MyList: {'yes' if add_to_mylist else 'no'}")
    console.print(f"- Mark watched: {'yes' if mark_watched else 'no'}")
    console.print(f"- Storage: {storage_label if storage_label else 'unchanged'}")

    # The final apply is the only gate that mutates AniDB; only an explicit "y" applies.
    if confirm("Apply MyList updates?") != "y":
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
            # R5: persist only the AniDB state code. Pass storage=None so the
            # free-text storage= field is omitted; the label is UI copy only
            # (shown in the summary above).
            try:
                ok, msg = client.mylist_add_or_update_by_fid(  # type: ignore[attr-defined]
                    info.fid,
                    add_to_mylist=add_to_mylist,
                    state=storage_state,
                    storage=None,
                    viewed=mark_watched,
                )
            except AniDBBannedError as exc:
                # A ban is not a per-file failure: stop the loop at once so no
                # further packet deepens the ban (issue #59).
                result.banned = True
                result.ban_reason = exc.reason
                break
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

    if result.banned:
        reason = f" ({result.ban_reason})" if result.ban_reason else ""
        console.print(
            f"[red]AniDB has banned this client{reason}.[/red] "
            "Stopped MyList updates before sending more; wait before retrying."
        )
        return result

    console.print("Applied.")
    return result
