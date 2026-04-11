"""Preview (Rich) and apply renames."""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.markup import escape as rich_escape
from rich.table import Table
from rich import box

from anipyrenamer.models import RenameItem, RenameKind

# First 1-4 digit number in filename (episode heuristic); used for display sort only.
_EPISODE_RE = re.compile(r"\d{1,4}")
_LOG = logging.getLogger(__name__)


def _plan_sort_key(item: RenameItem) -> tuple[str, int, str]:
    """Sort key for preview table: (folder_name_casefold, episode_int, path). SKIP items use old_path."""
    if item.kind == RenameKind.SKIP:
        p = Path(item.old_path)
    else:
        p = Path(item.new_path)
    folder = p.parent.name.casefold() if p.parent.name else ""
    stem = p.stem
    match = _EPISODE_RE.search(stem)
    episode = int(match.group()) if match else 0
    return (folder, episode, item.old_path)


def preview_plan(items: list[RenameItem], console: Console | None = None) -> None:
    """Print rename plan as a table (old_path -> new_path). Rows sorted by destination folder then episode."""
    out = console or Console()
    table = Table(
        title="Rename Plan",
        title_style="bold cyan",
        box=box.ROUNDED,
        border_style="dim",
    )
    table.add_column("Current", style="dim")
    table.add_column("New", style="green")
    table.add_column("Type", style="dim")  # Read-only: anime type (tv, movie, ova, web, etc.)
    for item in sorted(items, key=_plan_sort_key):
        type_display = item.anime_type or "—"
        # Escape brackets so Rich doesn't treat e.g. [Hi10] or [anidb-12345] as markup
        table.add_row(
            rich_escape(item.old_path),
            rich_escape(item.new_path),
            rich_escape(type_display),
        )
    out.print(table)


def _same_path(a: Path, b: Path) -> bool:
    """True if both paths exist and refer to the same file/dir (resolve for symlinks)."""
    if not a.exists() or not b.exists():
        return False
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def apply_plan(
    items: list[RenameItem],
    db_path: str,  # Reserved for future use (e.g. post-rename cache path update); not used today.
    *,
    dry_run: bool = False,
    progress_callback: Callable[[int, int, RenameItem, bool | None], None] | None = None,
) -> tuple[int, int]:
    """
    Move each file old_path to new_path; create parent dirs if needed.
    Only FILE items are applied. After moves, remove empty source directories
    (depth descending so parent dirs can become empty). No implicit overwrite:
    if destination already exists and is not the source, the item is skipped.
    If dry_run, do nothing and return (0, 0).
    progress_callback: optional (current_1based_index, total, item, skipped).
      Called at start of each item with skipped=None; at end with skipped=True/False for CLI progress UI.
    Returns (applied_count, skipped_count) for exit code semantics (exit 2 when skipped_count > 0).
    """
    if dry_run:
        return (0, 0)
    file_items = [i for i in items if i.kind == RenameKind.FILE]
    total = len(file_items)
    applied_source_parents: set[Path] = set()
    applied_count = 0
    skipped_count = 0
    for idx, item in enumerate(file_items, start=1):
        if progress_callback:
            progress_callback(idx, total, item, None)  # started
        src = Path(item.old_path)
        dst = Path(item.new_path)
        skipped = True
        if src.exists():
            if not dst.exists() or _same_path(src, dst):
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                applied_source_parents.add(src.parent)
                applied_count += 1
                skipped = False
            else:
                skipped_count += 1
        else:
            skipped_count += 1
        if progress_callback:
            progress_callback(idx, total, item, skipped)  # done
    _remove_empty_source_dirs(applied_source_parents)
    return (applied_count, skipped_count)


def _remove_empty_source_dirs(applied_source_parents: set[Path]) -> None:
    """
    Best-effort cleanup for empty source directories (deepest first).

    If the process is currently inside a source directory, move to the parent
    before attempting rmdir. Cleanup failures are non-fatal.
    """
    cwd = Path.cwd().resolve()
    for dir_path in sorted(applied_source_parents, key=lambda p: len(p.parts), reverse=True):
        if not dir_path.exists() or not dir_path.is_dir():
            continue
        try:
            if any(dir_path.iterdir()):
                continue
        except OSError as exc:
            _LOG.warning("Skipping source directory cleanup for %s: %s", dir_path, exc)
            continue

        try:
            resolved_dir = dir_path.resolve()
        except OSError:
            resolved_dir = dir_path
        if resolved_dir == cwd:
            try:
                os.chdir(str(dir_path.parent))
                cwd = Path.cwd().resolve()
            except OSError as exc:
                _LOG.warning(
                    "Could not leave source directory %s before cleanup: %s", dir_path, exc
                )
                continue

        try:
            dir_path.rmdir()
        except OSError as exc:
            _LOG.warning("Skipping source directory cleanup for %s: %s", dir_path, exc)
