"""Preview (Rich) and apply renames; record in rename_history."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from rich.console import Console
from rich.table import Table

from anipyrenamer.models import RenameItem


def preview_plan(items: list[RenameItem], console: Console | None = None) -> None:
    """Print rename plan as a table (old_path -> new_path)."""
    out = console or Console()
    table = Table(title="Rename plan")
    table.add_column("Current", style="dim")
    table.add_column("New", style="green")
    for item in items:
        table.add_row(item.old_path, item.new_path)
    out.print(table)


def apply_plan(
    items: list[RenameItem],
    db_path: str,
    *,
    dry_run: bool = False,
    record: bool = True,
    batch_id: str | None = None,
) -> None:
    """
    Move each old_path to new_path; create parent dirs if needed.
    Applies file renames first, then folder renames, so folder renames do not invalidate paths.
    If record is True, append to rename_history. If dry_run, do nothing.
    """
    if dry_run:
        return
    file_items: list[RenameItem] = []
    folder_items: list[RenameItem] = []
    for item in items:
        src = Path(item.old_path)
        if not src.exists():
            continue
        if src.is_file():
            file_items.append(item)
        else:
            folder_items.append(item)
    bid = batch_id or str(uuid.uuid4())
    to_record: list[tuple[str, str]] = []
    for item in file_items + folder_items:
        src = Path(item.old_path)
        dst = Path(item.new_path)
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.resolve() != src.resolve():
            dst.unlink()
        shutil.move(str(src), str(dst))
        to_record.append((item.old_path, item.new_path))
    if record and to_record:
        from anipyrenamer.cache import record_renames

        record_renames(db_path, to_record, batch_id=bid)
