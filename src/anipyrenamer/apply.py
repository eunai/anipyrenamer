"""Preview (Rich) and apply renames."""

from __future__ import annotations

import logging
import ntpath
import os
import posixpath
import re
import shutil
from pathlib import Path
from typing import Callable

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from anipyrenamer.models import RenameItem, RenameKind

# First 1-4 digit number in filename (episode heuristic); used for display sort only.
_EPISODE_RE = re.compile(r"\d{1,4}")
_LOG = logging.getLogger(__name__)


def _plan_sort_key(item: RenameItem) -> tuple[str, int, str]:
    """Legacy sort key: (folder_name_casefold, episode_int, path). SKIP items use old_path."""
    if item.kind == RenameKind.SKIP:
        p = Path(item.old_path)
    else:
        p = Path(item.new_path)
    folder = p.parent.name.casefold() if p.parent.name else ""
    stem = p.stem
    match = _EPISODE_RE.search(stem)
    episode = int(match.group()) if match else 0
    return (folder, episode, item.old_path)


def _is_windows_path(path: str) -> bool:
    return bool(ntpath.splitdrive(path)[0] or "\\" in path)


def _path_root(path: str) -> str:
    drive, tail = ntpath.splitdrive(path) if _is_windows_path(path) else posixpath.splitdrive(path)
    if drive:
        return drive.casefold()
    if tail.startswith(("/", "\\")):
        return "\\" if _is_windows_path(path) else "/"
    return ""


def _parent(path: str) -> str:
    return ntpath.dirname(path) if _is_windows_path(path) else posixpath.dirname(path)


def _basename(path: str) -> str:
    return ntpath.basename(path) if _is_windows_path(path) else posixpath.basename(path)


def _stem(path: str) -> str:
    return os.path.splitext(_basename(path))[0]


def _display_sep(path: str) -> str:
    return "\\" if _is_windows_path(path) else "/"


def _normalize_sort_path(path: str) -> str:
    return path.replace("\\", "/").casefold()


def _partition_by_filesystem_root(items: list[RenameItem]) -> dict[str, list[RenameItem]]:
    partitions: dict[str, list[RenameItem]] = {}
    for item in items:
        partition_path = item.old_path if item.kind == RenameKind.SKIP else item.new_path
        partitions.setdefault(_path_root(partition_path), []).append(item)
    return partitions


def _same_filesystem_root(path: str, plan_root: str) -> bool:
    return _path_root(path) == _path_root(plan_root)


def _compute_plan_root(items: list[RenameItem]) -> str:
    candidate_dirs: list[str] = []
    for item in items:
        primary_path = item.old_path if item.kind == RenameKind.SKIP else item.new_path
        primary_parent = _parent(primary_path)
        candidate_dirs.append(primary_parent)

        if item.kind != RenameKind.SKIP and _path_root(item.old_path) == _path_root(item.new_path):
            candidate_dirs.append(_parent(item.old_path))

    if not candidate_dirs:
        return ""

    try:
        if _is_windows_path(candidate_dirs[0]):
            return ntpath.commonpath(candidate_dirs)
        return posixpath.commonpath(candidate_dirs)
    except ValueError:
        if _is_windows_path(candidate_dirs[0]):
            drive, tail = ntpath.splitdrive(candidate_dirs[0])
            sep = "\\"
        else:
            drive, tail = posixpath.splitdrive(candidate_dirs[0])
            sep = "/"
        if drive:
            return drive + sep
        return sep if tail.startswith(("/", "\\")) else ""


def _relative_to_plan_root(path: str, plan_root: str) -> str:
    if not plan_root or not _same_filesystem_root(path, plan_root):
        return path

    try:
        rel_path = (
            ntpath.relpath(path, plan_root)
            if _is_windows_path(plan_root)
            else posixpath.relpath(path, plan_root)
        )
    except ValueError:
        return path
    return "" if rel_path == "." else rel_path


def _with_trailing_sep(path: str, sep_source: str) -> str:
    sep = _display_sep(sep_source)
    return path if path.endswith(("/", "\\")) else f"{path}{sep}"


def _split_display_path(path: str) -> tuple[list[str], str, bool]:
    sep = "\\" if "\\" in path else "/"
    trailing = path.endswith(("/", "\\"))
    stripped = path.rstrip("/\\")
    return ([part for part in re.split(r"[\\/]+", stripped) if part], sep, trailing)


def _segment_diff_colored(old: str, new: str) -> tuple[Text, Text]:
    old_parts, _, _ = _split_display_path(old)
    new_parts, sep, new_had_trailing_sep = _split_display_path(new)
    common_count = 0
    for old_part, new_part in zip(old_parts, new_parts):
        if old_part.casefold() != new_part.casefold():
            break
        common_count += 1

    current = Text(old, style="dim")
    new_text = Text()
    if not new_parts:
        new_text.append(new, style="dim")
        return current, new_text

    for idx, part in enumerate(new_parts):
        style = "dim" if idx < common_count else "green"
        new_text.append(part, style=style)
        if idx < len(new_parts) - 1 or new_had_trailing_sep:
            new_text.append(sep, style=style)
    return current, new_text


def _folder_sort_key(parent_str: str) -> str:
    return _normalize_sort_path(parent_str)


def _episode_int_from_stem(stem: str) -> int:
    match = _EPISODE_RE.search(stem)
    return int(match.group()) if match else 0


def _file_sort_key(item: RenameItem) -> tuple[int, str]:
    return (_episode_int_from_stem(_stem(item.new_path)), _normalize_sort_path(item.old_path))


def _skip_folder_sort_key(parent_str: str) -> str:
    return _normalize_sort_path(parent_str)


def _skip_file_sort_key(item: RenameItem) -> str:
    return _normalize_sort_path(item.old_path)


def _preview_table(*columns: str, header_style: str = "bold") -> Table:
    table = Table(
        box=box.MINIMAL,
        show_header=True,
        header_style=header_style,
        padding=(0, 1),
        pad_edge=False,
    )
    for column in columns:
        table.add_column(column)
    return table


def _section_title(
    console: Console, title: str, rendered_before: bool, style: str = "bold"
) -> None:
    if rendered_before:
        console.print()
    console.print(Text(title, style=style))


def _partition_separator(console: Console, partition_index: int) -> None:
    if partition_index:
        console.print()


def _render_folders_section(
    items_by_partition: dict[str, list[RenameItem]], console: Console, *, rendered_before: bool
) -> bool:
    rendered = False
    section_index = 0
    for partition_items in [
        items_by_partition[key] for key in sorted(items_by_partition, key=_normalize_sort_path)
    ]:
        plan_root = _compute_plan_root(partition_items)
        buckets: dict[tuple[str, str], RenameItem] = {}
        for item in partition_items:
            old_parent = _parent(item.old_path)
            new_parent = _parent(item.new_path)
            if old_parent == new_parent:
                continue
            buckets.setdefault((old_parent, new_parent), item)

        if not buckets:
            continue

        if not rendered:
            _section_title(console, "Folders", rendered_before)
            rendered = True
        _partition_separator(console, section_index)
        section_index += 1
        console.print(Text(plan_root, style="dim"))

        table = _preview_table("Current", "New", "Type")
        for (old_parent, new_parent), first_item in sorted(
            buckets.items(), key=lambda row: _folder_sort_key(row[0][1])
        ):
            old_display = _with_trailing_sep(
                _relative_to_plan_root(old_parent, plan_root), plan_root
            )
            new_display = _with_trailing_sep(
                _relative_to_plan_root(new_parent, plan_root), plan_root
            )
            current, new = _segment_diff_colored(old_display, new_display)
            table.add_row(current, new, Text(first_item.anime_type or "—", style="dim"))
        console.print(table)

    return rendered


def _branch_cell(branch: str, value: Text) -> Text:
    text = Text(f"{branch} ", style="dim")
    text.append_text(value)
    return text


def _render_files_section(
    items_by_partition: dict[str, list[RenameItem]], console: Console, *, rendered_before: bool
) -> bool:
    if not any(items_by_partition.values()):
        return False

    rendered = False
    section_index = 0
    for partition_items in [
        items_by_partition[key] for key in sorted(items_by_partition, key=_normalize_sort_path)
    ]:
        if not partition_items:
            continue

        if not rendered:
            _section_title(console, "Files", rendered_before)
            rendered = True
        _partition_separator(console, section_index)
        section_index += 1

        plan_root = _compute_plan_root(partition_items)
        console.print(Text(plan_root, style="dim"))

        table = _preview_table("Current", "New")
        buckets: dict[tuple[str, str], list[RenameItem]] = {}
        for item in partition_items:
            buckets.setdefault((_parent(item.old_path), _parent(item.new_path)), []).append(item)

        for (old_parent, new_parent), bucket_items in sorted(
            buckets.items(), key=lambda row: _folder_sort_key(row[0][1])
        ):
            if new_parent != plan_root:
                old_header = _with_trailing_sep(
                    _relative_to_plan_root(old_parent, plan_root), plan_root
                )
                new_header = _with_trailing_sep(
                    _relative_to_plan_root(new_parent, plan_root), plan_root
                )
                current, new = _segment_diff_colored(old_header, new_header)
                table.add_row(current, new)

            sorted_items = sorted(bucket_items, key=_file_sort_key)
            for idx, item in enumerate(sorted_items):
                branch = "└─" if idx == len(sorted_items) - 1 else "├─"
                current, new = _segment_diff_colored(
                    _basename(item.old_path), _basename(item.new_path)
                )
                table.add_row(_branch_cell(branch, current), _branch_cell(branch, new))

        console.print(table)

    return rendered


def _skipped_text(value: str) -> Text:
    return Text(value, style="italic dim")


def _skipped_branch_cell(branch: str, value: str) -> Text:
    return Text(f"{branch} {value}", style="italic dim")


def _render_skipped_section(
    skip_items_by_partition: dict[str, list[RenameItem]],
    console: Console,
    *,
    rendered_before: bool,
) -> bool:
    if not any(skip_items_by_partition.values()):
        return False

    _section_title(console, "Files (skipped)", rendered_before, style="bold dim")
    section_index = 0
    for partition_items in [
        skip_items_by_partition[key]
        for key in sorted(skip_items_by_partition, key=_normalize_sort_path)
    ]:
        if not partition_items:
            continue
        _partition_separator(console, section_index)
        section_index += 1

        plan_root = _compute_plan_root(partition_items)
        console.print(Text(plan_root, style="italic dim"))

        table = _preview_table("Current", "New", header_style="bold dim")
        buckets: dict[str, list[RenameItem]] = {}
        for item in partition_items:
            buckets.setdefault(_parent(item.old_path), []).append(item)

        for old_parent, bucket_items in sorted(
            buckets.items(), key=lambda row: _skip_folder_sort_key(row[0])
        ):
            if old_parent != plan_root:
                header = _with_trailing_sep(
                    _relative_to_plan_root(old_parent, plan_root), plan_root
                )
                table.add_row(_skipped_text(header), _skipped_text(header))

            sorted_items = sorted(bucket_items, key=_skip_file_sort_key)
            for idx, item in enumerate(sorted_items):
                branch = "└─" if idx == len(sorted_items) - 1 else "├─"
                table.add_row(
                    _skipped_branch_cell(branch, _basename(item.old_path)),
                    _skipped_branch_cell(branch, "(AniDB lookup failed)"),
                )
        console.print(table)

    return True


def preview_plan(items: list[RenameItem], console: Console | None = None) -> None:
    """Print rename plan as plan-root-factored Folders, Files, and skipped sections."""
    out = console or Console()
    if not items:
        return

    file_items = [item for item in items if item.kind != RenameKind.SKIP]
    skip_items = [item for item in items if item.kind == RenameKind.SKIP]
    file_by_partition = _partition_by_filesystem_root(file_items)
    skip_by_partition = _partition_by_filesystem_root(skip_items)

    rendered = _render_folders_section(file_by_partition, out, rendered_before=False)
    rendered = _render_files_section(file_by_partition, out, rendered_before=rendered) or rendered
    _render_skipped_section(skip_by_partition, out, rendered_before=rendered)


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
