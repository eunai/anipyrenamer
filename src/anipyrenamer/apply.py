"""Apply renames. (Plan preview rendering lives in the ledger — SPEC §3, #51.)"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from anipyrenamer.models import RenameItem, RenameKind

_LOG = logging.getLogger(__name__)


def _same_path(a: Path, b: Path) -> bool:
    """True if both paths exist and refer to the same file/dir (resolve for symlinks)."""
    if not a.exists() or not b.exists():
        return False
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


@dataclass(frozen=True)
class ApplyResult:
    """Apply outcome with the skip-reason breakdown (SPEC §5).

    ``applied + skipped_total`` reconciles against the attempted FILE items;
    a nonzero ``skipped_total`` signals partial completion (exit 2, SPEC §4).
    Reasons are categories, never filenames (SPEC §7).
    """

    applied: int
    skipped_destination_exists: int
    skipped_source_missing: int

    @property
    def skipped_total(self) -> int:
        return self.skipped_destination_exists + self.skipped_source_missing


def apply_plan(
    items: list[RenameItem],
    db_path: str,  # Reserved for future use (e.g. post-rename cache path update); not used today.
    *,
    dry_run: bool = False,
) -> ApplyResult:
    """
    Move each file old_path to new_path; create parent dirs if needed.
    Only FILE items are applied. After moves, remove empty source directories
    (depth descending so parent dirs can become empty). No implicit overwrite:
    if destination already exists and is not the source, the item is skipped.
    If dry_run, do nothing and return a zeroed ApplyResult.
    Returns an ApplyResult whose skip counts carry the reason breakdown
    (destination-exists vs source-missing) for the apply counter row and the
    exit code semantics (exit 2 when skipped_total > 0).
    """
    if dry_run:
        return ApplyResult(0, 0, 0)
    file_items = [i for i in items if i.kind == RenameKind.FILE]
    applied_source_parents: set[Path] = set()
    applied_count = 0
    dest_exists_count = 0
    source_missing_count = 0
    for item in file_items:
        src = Path(item.old_path)
        dst = Path(item.new_path)
        if src.exists():
            if not dst.exists() or _same_path(src, dst):
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                applied_source_parents.add(src.parent)
                applied_count += 1
            else:
                dest_exists_count += 1
        else:
            source_missing_count += 1
    _remove_empty_source_dirs(applied_source_parents)
    return ApplyResult(applied_count, dest_exists_count, source_missing_count)


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
