"""The Quiet Ledger renderer and run-summary footer (design-locked via map #45).

A single :class:`Ledger` instance owns *all* phase-gutter output and the
run-summary footer for one run, so parity discipline lives in one place and the
streamed counter line and the footer's recap row are the same formatted string
from one formatter. Rendered through the injected Rich ``Console`` with
``soft_wrap=True`` so the emitted byte stream is width-independent (SPEC §3).
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from enum import Enum
from pathlib import Path

from rich.console import Console
from rich.markup import escape as rich_escape

from anipyrenamer.models import RenameItem, RenameKind

# Fixed-width rule line: a literal string, never width-derived, so TTY and piped
# output are byte-identical after ANSI stripping (SPEC §3 parity).
RULE = "─" * 34

# First 1-4 digit number in filename (episode heuristic); used for display sort only.
_EPISODE_RE = re.compile(r"\d{1,4}")


def _category_phrase(counts: Sequence[tuple[int, str]]) -> str:
    """Homogeneous reasons render as the bare category; mixed reasons break down per
    category (SPEC §5). Reasons are categories, never filenames (SPEC §7)."""
    present = [(n, category) for n, category in counts if n]
    if len(present) == 1:
        return present[0][1]
    return " · ".join(f"{n} {category}" for n, category in present)


def _plan_sort_key(item: RenameItem) -> tuple[str, int, str]:
    """Sort key for the plan block: (folder_name_casefold, episode_int, path). SKIP items use old_path."""
    if item.kind == RenameKind.SKIP:
        p = Path(item.old_path)
    else:
        p = Path(item.new_path)
    folder = p.parent.name.casefold() if p.parent.name else ""
    stem = p.stem
    match = _EPISODE_RE.search(stem)
    episode = int(match.group()) if match else 0
    return (folder, episode, item.old_path)


def _common_root(dest_paths: Sequence[str]) -> str | None:
    """Deepest common ancestor of the planned destinations (SPEC §3 `→` factoring).

    A single destination factors to its parent folder. Returns ``None`` when
    there are no destinations or no common ancestor exists (cross-volume — the
    degenerate case #46 notes is unreachable in practice; rename lines then
    carry their full destination paths and the footer omits its `→` line).
    """
    if not dest_paths:
        return None
    parents = [os.path.dirname(p) for p in dest_paths]
    try:
        return os.path.commonpath(parents)
    except ValueError:
        return None


class RunOutcome(Enum):
    """The nine ``(exit code, verdict)`` rows of SPEC §4.

    Each member is ``(exit_code, verdict_text)``. The verdict line leads with the
    literal ``exit N`` and only *explains* the code; it never sets a different one
    (the exit code and the verdict share this single source of truth).
    """

    APPLIED_CLEAN = (0, "all renames applied")
    DRY_RUN_CLEAN = (0, "dry run — nothing changed")
    DECLINED = (0, "not applied — nothing changed")
    NO_MATCHES = (0, "no files to rename")
    APPLIED_WITH_SKIPS = (2, "completed with skips — review and re-run")
    DRY_RUN_CONFLICTS = (2, "dry run — conflicts flagged; resolve before applying")
    MYLIST_FAILED = (2, "renames applied; some MyList updates failed")
    CONFLICT_FAIL_ABORT = (1, "aborted on destination conflicts (--on-conflict=fail)")
    INTERRUPTED = (130, "interrupted")

    def __init__(self, exit_code: int, text: str) -> None:
        self._exit_code = exit_code
        self._text = text

    @property
    def exit_code(self) -> int:
        """The integer this run exits with (the value ``$?`` receives)."""
        return self._exit_code

    @property
    def verdict(self) -> str:
        """The single context-adaptive verdict line, leading with ``exit N``."""
        return f"exit {self._exit_code} · {self._text}"


class Ledger:
    """Owns the phase-gutter stream and the run-summary footer for one run.

    Each counter method prints one permanent gutter line once its phase has
    settled and stores the same (label, content) pair; the footer replays the
    stored pairs through the same formatter, so the streamed counter line and
    the footer's recap row are byte-identical by construction (SPEC §3).
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        self._rows: list[tuple[str, str]] = []  # settled (label, content), footer recap order
        # Plan facts stored for the footer (the `→` line and the dry-run/declined action row).
        self._plan_root: str | None = None
        self._plan_dry_run = False
        self._plan_to_rename = 0
        self._plan_flagged_dest_exists = 0
        self._plan_flagged_collision = 0
        self._declined = False

    def _print_row(self, label: str, content: str) -> None:
        """The one gutter formatter: 2-space margin, 9-wide dim label, 2-space gap."""
        self._console.print(f"  [dim]{label:<9}[/dim]  {content}", soft_wrap=True, highlight=False)

    def _stream_counter(self, label: str, content: str) -> None:
        self._rows.append((label, content))
        self._print_row(label, content)

    def discover(self, found: int) -> None:
        """One settled line for the discovery phase: ``discover   N found``."""
        self._stream_counter("discover", f"{found:>2} found")

    def hash_lookup(self, *, cached: int, fetched: int, no_match: int) -> None:
        """One settled line for hash+look — per-file chatter collapses into this counter."""
        self._stream_counter(
            "hash+look", f"{cached:>2} cached · {fetched} fetched · {no_match} no match"
        )

    def plan(
        self,
        items: list[RenameItem],
        *,
        conflict_indexes: frozenset[int] = frozenset(),
        folder_conflicts: Sequence[str] = (),
        dry_run: bool = False,
        render_block: bool = True,
    ) -> None:
        """Emit the plan block: warnings, the factored `→` header, and the flat item lines.

        Absorbs the old Rich-``Table`` preview (SPEC §3). ``render_block=False``
        (``--preview-format json``) still computes and stores the plan facts and
        prints the phase header, but leaves the preview to the JSON dump.
        """
        indexed_files = [(i, it) for i, it in enumerate(items) if it.kind == RenameKind.FILE]
        skips = [it for it in items if it.kind == RenameKind.SKIP]
        renames = [it for i, it in indexed_files if i not in conflict_indexes]
        flagged = [it for i, it in indexed_files if i in conflict_indexes]
        flagged_reasons = [
            (it, "destination exists" if Path(it.new_path).exists() else "destination collision")
            for it in flagged
        ]

        root = _common_root([it.new_path for _, it in indexed_files])
        self._plan_root = root
        self._plan_dry_run = dry_run
        self._plan_to_rename = len(renames)
        self._plan_flagged_dest_exists = sum(
            1 for _, reason in flagged_reasons if reason == "destination exists"
        )
        self._plan_flagged_collision = len(flagged_reasons) - self._plan_flagged_dest_exists

        # Folder-level conflicts: one inline warning line each, above the block — no Panel.
        for msg in folder_conflicts:
            self._console.print(
                f"[dim yellow]! {rich_escape(msg)}[/dim yellow]", soft_wrap=True, highlight=False
            )

        if root is None:
            self._console.print("  [dim]plan[/dim]", soft_wrap=True, highlight=False)
        else:
            planned = " (planned)" if dry_run else ""
            self._print_row("plan", f"→ {rich_escape(root)}{planned}")

        if not render_block:
            return

        # Flat block: renames first, then skipped/flagged inline — dim, category
        # reason, no `→` (SPEC §3/§5). Left column pads to the longest basename;
        # the padding is content-derived, never console-width-derived (parity).
        tail = sorted(
            [(it, "skipped", "no match") for it in skips]
            + [(it, "flagged", reason) for it, reason in flagged_reasons],
            key=lambda entry: _plan_sort_key(entry[0]),
        )
        names = [os.path.basename(it.old_path) for it in renames] + [
            os.path.basename(it.old_path) for it, _, _ in tail
        ]
        width = max((len(n) for n in names), default=0)
        for it in sorted(renames, key=_plan_sort_key):
            name = os.path.basename(it.old_path)
            pad = " " * (width - len(name))
            target = it.new_path if root is None else os.path.relpath(it.new_path, root)
            self._print_row(
                "",
                f"[dim]{rich_escape(name)}[/dim]{pad}  →  [green]{rich_escape(target)}[/green]",
            )
        for it, verb, reason in tail:
            name = os.path.basename(it.old_path)
            pad = " " * (width - len(name))
            self._print_row("", f"[dim]{rich_escape(name)}{pad}     {verb} · {reason}[/dim]")

    def apply(
        self, *, renamed: int, dest_exists: int, source_missing: int, apply_failed: int = 0
    ) -> None:
        """One settled line for apply, with the category skip-reason breakdown (SPEC §5)."""
        skipped = dest_exists + source_missing + apply_failed
        content = f"{renamed:>2} renamed · {skipped} skipped"
        if skipped:
            content += f" ({_category_phrase([(dest_exists, 'destination exists'), (source_missing, 'source missing'), (apply_failed, 'apply failed')])})"
        self._stream_counter("apply", content)

    def mylist(self, added: int) -> None:
        """One settled line for the MyList wizard (appears only under --mylist)."""
        self._stream_counter("mylist", f"+{added} added")

    def declined(self) -> None:
        """Record that the confirm gate declined the apply (drives the footer action row)."""
        self._declined = True

    def _plan_action_row(self) -> str | None:
        """The footer-only action row for runs where apply never streamed one (SPEC §3).

        Declined → ``N to rename · not applied (declined)``; dry-run →
        ``N to rename · M flagged (reason)``. Runs with no plan facts get none.
        """
        if self._declined:
            return f"{self._plan_to_rename:>2} to rename · not applied (declined)"
        flagged = self._plan_flagged_dest_exists + self._plan_flagged_collision
        if self._plan_dry_run and (self._plan_to_rename or flagged):
            content = f"{self._plan_to_rename:>2} to rename · {flagged} flagged"
            if flagged:
                content += f" ({_category_phrase([(self._plan_flagged_dest_exists, 'destination exists'), (self._plan_flagged_collision, 'destination collision')])})"
            return content
        return None

    def footer(self, outcome: RunOutcome) -> None:
        """Print the run-summary footer, closing with the verdict line (SPEC §4).

        The action row is the furthest phase reached: a streamed ``apply`` row
        when apply ran, else the footer-only ``plan`` row for dry-run/declined
        runs. Unused rows are omitted, never zero-padded.
        """
        rows = list(self._rows)
        if not any(label == "apply" for label, _ in rows):
            action = self._plan_action_row()
            if action is not None:
                # The plan action row sits where the apply row would: before a
                # mylist row when one streamed (the wizard runs under --dry-run).
                mylist_at = next(
                    (i for i, (label, _) in enumerate(rows) if label == "mylist"), None
                )
                rows.insert(len(rows) if mylist_at is None else mylist_at, ("plan", action))
        self._console.print(f"[dim]{RULE}[/dim]", soft_wrap=True)
        if self._plan_root is not None:
            planned = " (planned)" if self._plan_dry_run else ""
            self._console.print(
                f"  → {rich_escape(self._plan_root)}{planned}", soft_wrap=True, highlight=False
            )
        for label, content in rows:
            self._print_row(label, content)
        self._console.print(f"[dim]{RULE}[/dim]", soft_wrap=True)
        self._console.print(f"  {outcome.verdict}", soft_wrap=True, highlight=False)

    def verdict_only(self, outcome: RunOutcome) -> None:
        """The degraded post-plan-abort form: the verdict line alone (SPEC §3/§4)."""
        self._console.print(f"  [red]{outcome.verdict}[/red]", soft_wrap=True, highlight=False)
