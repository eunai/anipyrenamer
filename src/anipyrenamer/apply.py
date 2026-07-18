"""Apply renames. (Plan preview rendering lives in the ledger — SPEC §3, #51.)"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from anipyrenamer.models import RenameItem, RenameKind

_LOG = logging.getLogger(__name__)

# Windows sharing/lock violations (ERROR_SHARING_VIOLATION / ERROR_LOCK_VIOLATION) are
# the only signal treated as a transient, retryable lock (issue #55). getattr(exc,
# "winerror", None) is None on POSIX and for any other OSError, so this is naturally
# a no-op off Windows rather than needing separate platform branching.
_LOCK_WINERRORS = frozenset({32, 33})
# Backoff between attempts: initial attempt + 2 retries (3 attempts total).
_RETRY_DELAYS_SECONDS = (0.25, 0.5)


def _move_with_retry(src: str, dst: str) -> None:
    """Move src to dst, retrying a Windows sharing/lock violation per the
    _RETRY_DELAYS_SECONDS backoff budget. Any other OSError (bad destination
    parent, read-only target, disk-full, invalid path) is not a transient
    condition and propagates on the first attempt."""
    attempts = len(_RETRY_DELAYS_SECONDS) + 1
    for attempt in range(attempts):
        try:
            shutil.move(src, dst)
        except OSError as exc:
            if getattr(exc, "winerror", None) not in _LOCK_WINERRORS or attempt == attempts - 1:
                raise
            time.sleep(_RETRY_DELAYS_SECONDS[attempt])
        else:
            return


def _log_apply_failure(src: Path, dst: Path, exc: OSError, *, dst_exists_after: bool) -> None:
    """Log an apply-time filesystem failure at a severity matching post-failure state:
    warning for a clean pre-write failure, error with a manual-review call-out when the
    destination exists after the failure (SPEC §5)."""
    if dst_exists_after:
        _LOG.error(
            "Apply failed (destination exists after failure; manual review required): %s -> %s: %s",
            src,
            dst,
            exc,
        )
    else:
        _LOG.warning("Apply failed: %s -> %s: %s", src, dst, exc)


def _same_path(a: Path, b: Path) -> bool:
    """True if both paths exist and refer to the same file/dir (resolve for symlinks)."""
    if not a.exists() or not b.exists():
        return False
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


@dataclass(frozen=True)
class ApplyFailure:
    """Per-item evidence for a ``skipped_apply_failed`` outcome (SPEC §5).

    ``dst_exists_after``/``src_exists_after`` are the post-catch filesystem
    state, not an inference: ``shutil.move``'s cross-device fallback
    (copy-then-remove) can fail *after* writing ``dst``, so ``dst`` existing
    does not imply its content is complete — only that manual review is
    warranted before a rerun silently re-surfaces the item as an
    unremarkable ``destination exists`` skip.
    """

    src: str
    dst: str
    reason: str
    src_exists_after: bool
    dst_exists_after: bool


@dataclass(frozen=True)
class ApplyResult:
    """Apply outcome with the skip-reason breakdown (SPEC §5).

    ``applied + skipped_total`` reconciles against the attempted FILE items;
    a nonzero ``skipped_total`` signals partial completion (exit 2, SPEC §4).
    Reasons are categories, never filenames (SPEC §7); per-item evidence for
    ``skipped_apply_failed`` lives in ``failures``.
    """

    applied: int
    skipped_destination_exists: int
    skipped_source_missing: int
    skipped_apply_failed: int
    failures: tuple[ApplyFailure, ...]

    @property
    def skipped_total(self) -> int:
        return (
            self.skipped_destination_exists
            + self.skipped_source_missing
            + self.skipped_apply_failed
        )


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
    (destination-exists / source-missing / apply-failed) for the apply
    counter row and the exit code semantics (exit 2 when skipped_total > 0).

    Apply-time filesystem failures (an OSError from creating the destination
    parent or from the move itself — e.g. a Windows PermissionError because
    the destination is held open by another process) are safe-fail, not
    transactional: the item is counted under skipped_apply_failed and the
    batch continues with the next item. No cleanup or rollback is attempted;
    shutil.move's cross-device fallback (copy then remove) can fail after
    writing the destination, so both src and dst may exist afterward (SPEC
    §5). A non-OSError exception is a bug, not an environmental condition,
    and still propagates and aborts the run.
    """
    if dry_run:
        return ApplyResult(
            applied=0,
            skipped_destination_exists=0,
            skipped_source_missing=0,
            skipped_apply_failed=0,
            failures=(),
        )
    file_items = [i for i in items if i.kind == RenameKind.FILE]
    applied_source_parents: set[Path] = set()
    applied_count = 0
    dest_exists_count = 0
    source_missing_count = 0
    apply_failed_count = 0
    failures: list[ApplyFailure] = []
    for item in file_items:
        src = Path(item.old_path)
        dst = Path(item.new_path)
        if src.exists():
            if not dst.exists() or _same_path(src, dst):
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    _move_with_retry(str(src), str(dst))
                except OSError as exc:
                    apply_failed_count += 1
                    dst_exists_after = dst.exists()
                    failures.append(
                        ApplyFailure(
                            src=str(src),
                            dst=str(dst),
                            reason=str(exc),
                            src_exists_after=src.exists(),
                            dst_exists_after=dst_exists_after,
                        )
                    )
                    _log_apply_failure(src, dst, exc, dst_exists_after=dst_exists_after)
                else:
                    applied_source_parents.add(src.parent)
                    applied_count += 1
            else:
                dest_exists_count += 1
        else:
            source_missing_count += 1
    _remove_empty_source_dirs(applied_source_parents)
    return ApplyResult(
        applied=applied_count,
        skipped_destination_exists=dest_exists_count,
        skipped_source_missing=source_missing_count,
        skipped_apply_failed=apply_failed_count,
        failures=tuple(failures),
    )


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
