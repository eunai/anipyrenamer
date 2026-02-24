"""Preview (Rich) and apply renames."""

from __future__ import annotations

import shutil
from pathlib import Path

from rich.console import Console
from rich.table import Table

from anipyrenamer.models import RenameItem, RenameKind


def preview_plan(items: list[RenameItem], console: Console | None = None) -> None:
    """Print rename plan as a table (old_path -> new_path)."""
    out = console or Console()
    table = Table(title="Rename plan")
    table.add_column("Current", style="dim")
    table.add_column("New", style="green")
    for item in items:
        table.add_row(item.old_path, item.new_path)
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
    db_path: str,
    *,
    dry_run: bool = False,
) -> None:
    """
    Move each file old_path to new_path; create parent dirs if needed.
    Only FILE items are applied. After moves, remove empty source directories
    (depth descending so parent dirs can become empty). No implicit overwrite:
    if destination already exists and is not the source, the item is skipped.
    If dry_run, do nothing.
    """
    if dry_run:
        return
    file_items = [i for i in items if i.kind == RenameKind.FILE]
    applied_source_parents: set[Path] = set()
    for item in file_items:
        src = Path(item.old_path)
        dst = Path(item.new_path)
        if not src.exists():
            continue
        if dst.exists() and not _same_path(src, dst):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        applied_source_parents.add(src.parent)
    # Remove empty source dirs (deepest first)
    for dir_path in sorted(applied_source_parents, key=lambda p: len(p.parts), reverse=True):
        if dir_path.exists() and dir_path.is_dir() and not any(dir_path.iterdir()):
            dir_path.rmdir()
