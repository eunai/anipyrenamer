"""Tests for the Quiet Ledger renderer and run-summary footer (map #45 / #51)."""

from __future__ import annotations

import io
import os
from pathlib import Path

from rich.console import Console

from anipyrenamer.ledger import RULE, Ledger, RunOutcome
from anipyrenamer.models import RenameItem, RenameKind


def _plain_console() -> tuple[Console, io.StringIO]:
    """A non-TTY, no-color Console writing to a StringIO buffer."""
    buf = io.StringIO()
    return Console(file=buf, no_color=True, width=80), buf


def _abs(*parts: str) -> str:
    """An absolute path literal with native separators (no drive; both platforms)."""
    return os.sep + os.sep.join(parts)


def test_run_outcome_verdict_leads_with_exit_code() -> None:
    """Each RunOutcome carries its exit code and a verdict that leads with `exit N`."""
    assert RunOutcome.APPLIED_CLEAN.exit_code == 0
    assert RunOutcome.APPLIED_CLEAN.verdict == "exit 0 · all renames applied"


def test_footer_prints_applied_clean_verdict() -> None:
    """The footer closes with the context-adaptive verdict line (SPEC §4)."""
    console, buf = _plain_console()
    ledger = Ledger(console)
    ledger.footer(RunOutcome.APPLIED_CLEAN)
    assert "exit 0 · all renames applied" in buf.getvalue()


def test_discover_counter_row_format() -> None:
    """`discover` emits one gutter line: 2-space margin, 9-wide label, counter content."""
    console, buf = _plain_console()
    Ledger(console).discover(13)
    assert buf.getvalue() == "  discover   13 found\n"


def test_hash_lookup_counter_row_format() -> None:
    """`hash+look` collapses per-file chatter into one settled counter line (SPEC §3)."""
    console, buf = _plain_console()
    Ledger(console).hash_lookup(cached=8, fetched=4, no_match=1)
    assert buf.getvalue() == "  hash+look   8 cached · 4 fetched · 1 no match\n"


def test_gutter_label_column_is_fixed_width() -> None:
    """The gutter label column is padded to `hash+look` (9 chars): content aligns across rows."""
    console, buf = _plain_console()
    ledger = Ledger(console)
    ledger.discover(13)
    ledger.hash_lookup(cached=8, fetched=4, no_match=1)
    lines = buf.getvalue().splitlines()
    # Content column starts at the same index on every gutter line: 2 + 9 + 2.
    assert lines[0][13:] == "13 found"
    assert lines[1][13:] == " 8 cached · 4 fetched · 1 no match"


def test_apply_counter_row_homogeneous_reason() -> None:
    """A single skip reason renders as the bare category (SPEC §5)."""
    console, buf = _plain_console()
    Ledger(console).apply(renamed=11, dest_exists=1, source_missing=0)
    assert buf.getvalue() == "  apply      11 renamed · 1 skipped (destination exists)\n"


def test_apply_counter_row_source_missing_reason() -> None:
    console, buf = _plain_console()
    Ledger(console).apply(renamed=5, dest_exists=0, source_missing=2)
    assert buf.getvalue() == "  apply       5 renamed · 2 skipped (source missing)\n"


def test_apply_counter_row_mixed_reasons() -> None:
    """Mixed reasons render the per-category breakdown (SPEC §5)."""
    console, buf = _plain_console()
    Ledger(console).apply(renamed=10, dest_exists=1, source_missing=1)
    assert (
        buf.getvalue()
        == "  apply      10 renamed · 2 skipped (1 destination exists · 1 source missing)\n"
    )


def test_apply_counter_row_no_skips_has_no_reason() -> None:
    """Zero skips renders no reason parenthetical (no empty parens)."""
    console, buf = _plain_console()
    Ledger(console).apply(renamed=3, dest_exists=0, source_missing=0)
    assert buf.getvalue() == "  apply       3 renamed · 0 skipped\n"


def test_plan_block_factors_root_and_renders_relative_renames() -> None:
    """The plan header carries the deepest common ancestor; renames are relative to it."""
    console, buf = _plain_console()
    items = [
        RenameItem(
            _abs("in", "cb1.mkv"),
            _abs("media", "Anime", "Cowboy Bebop", "Cowboy Bebop - 01.mkv"),
            kind=RenameKind.FILE,
        ),
        RenameItem(
            _abs("in", "tr1.mkv"),
            _abs("media", "Anime", "Trigun", "Trigun - 01.mkv"),
            kind=RenameKind.FILE,
        ),
    ]
    Ledger(console).plan(items)
    lines = buf.getvalue().splitlines()
    root = _abs("media", "Anime")
    rel_cb = os.sep.join(("Cowboy Bebop", "Cowboy Bebop - 01.mkv"))
    rel_tr = os.sep.join(("Trigun", "Trigun - 01.mkv"))
    assert lines[0] == f"  plan       → {root}"
    assert lines[1] == f"             cb1.mkv  →  {rel_cb}"
    assert lines[2] == f"             tr1.mkv  →  {rel_tr}"


def test_plan_root_single_series_folder_is_that_folder() -> None:
    """A single destination folder factors to that folder, not its parent."""
    console, buf = _plain_console()
    items = [RenameItem(_abs("in", "a.mkv"), _abs("media", "Show", "a.mkv"), kind=RenameKind.FILE)]
    Ledger(console).plan(items)
    assert buf.getvalue().splitlines()[0] == f"  plan       → {_abs('media', 'Show')}"


def test_plan_header_carries_planned_suffix_under_dry_run() -> None:
    """` (planned)` marks the factored root under --dry-run (SPEC §3)."""
    console, buf = _plain_console()
    items = [RenameItem(_abs("in", "a.mkv"), _abs("media", "Show", "a.mkv"), kind=RenameKind.FILE)]
    Ledger(console).plan(items, dry_run=True)
    assert buf.getvalue().splitlines()[0] == f"  plan       → {_abs('media', 'Show')} (planned)"


def test_plan_block_lists_skipped_and_flagged_inline_after_renames() -> None:
    """No-match and conflict-flagged items list inline, dim, category reason, no arrow."""
    console, buf = _plain_console()
    items = [
        RenameItem(
            _abs("in", "good.mkv"), _abs("media", "Show", "Show - 01.mkv"), kind=RenameKind.FILE
        ),
        RenameItem(_abs("in", "mystery.mkv"), "(AniDB lookup failed)", kind=RenameKind.SKIP),
        RenameItem(
            _abs("in", "dup.mkv"), _abs("media", "Show", "Show - 02.mkv"), kind=RenameKind.FILE
        ),
    ]
    Ledger(console).plan(items, conflict_indexes=frozenset({2}))
    lines = buf.getvalue().splitlines()
    # Both destinations share the Show folder, so the root factors to it and
    # rename targets are bare filenames. Left column pads to the longest
    # basename (mystery.mkv, 11 chars).
    assert lines[0] == f"  plan       → {_abs('media', 'Show')}"
    assert lines[1] == "             good.mkv     →  Show - 01.mkv"
    assert lines[2] == "             mystery.mkv     skipped · no match"
    assert lines[3] == "             dup.mkv         flagged · destination collision"


def test_plan_flagged_destination_exists_reason(tmp_path: Path) -> None:
    """A conflict whose destination exists on disk is flagged `destination exists`."""
    console, buf = _plain_console()
    occupied = tmp_path / "occupied.mkv"
    occupied.write_bytes(b"x")
    items = [RenameItem(str(tmp_path / "src.mkv"), str(occupied), kind=RenameKind.FILE)]
    Ledger(console).plan(items, conflict_indexes=frozenset({0}))
    assert "flagged · destination exists" in buf.getvalue()


def test_plan_folder_conflicts_render_inline_without_panel() -> None:
    """Folder-level conflicts print as one inline warning line above the block — no Panel."""
    console, buf = _plain_console()
    items = [RenameItem(_abs("in", "a.mkv"), _abs("media", "Show", "a.mkv"), kind=RenameKind.FILE)]
    Ledger(console).plan(items, folder_conflicts=["two source folders map to Show — merge"])
    lines = buf.getvalue().splitlines()
    assert lines[0] == "! two source folders map to Show — merge"
    assert lines[1].startswith("  plan")
    assert "╭" not in buf.getvalue() and "│" not in buf.getvalue()  # no Panel chrome


def test_plan_block_sorted_by_destination_folder_then_episode() -> None:
    """Plan order is destination folder (casefolded) then first episode number (preserved)."""
    console, buf = _plain_console()
    items = [
        RenameItem(
            _abs("any", "222222.mkv"),
            _abs("anime", "Dan Da Dan [Subs]", "Dan Da Dan 01 - First [Subs].mkv"),
        ),
        RenameItem(
            _abs("any", "11.mkv"),
            _abs("anime", "Blue Lock [SEV]", "Blue Lock 02 - Monster [SEV].mkv"),
        ),
        RenameItem(
            _abs("any", "other.mkv"),
            _abs("anime", "Blue Lock [SEV]", "Blue Lock 01 - Dream [SEV].mkv"),
        ),
        RenameItem(
            _abs("any", "x.mkv"),
            _abs("anime", "Nana [EMBER]", "Nana 01 - Prologue [EMBER].mkv"),
        ),
    ]
    Ledger(console).plan(items)
    text = buf.getvalue()
    positions = [
        text.index("Blue Lock 01"),
        text.index("Blue Lock 02"),
        text.index("Dan Da Dan 01"),
        text.index("Nana 01"),
    ]
    assert positions == sorted(positions)
    assert "[Subs]" in text  # markup-escaped bracket groups render literally


def test_mylist_counter_row_format() -> None:
    """`mylist` emits one settled `+N added` line (only under --mylist)."""
    console, buf = _plain_console()
    Ledger(console).mylist(11)
    assert buf.getvalue() == "  mylist     +11 added\n"


def test_verdict_table_matches_spec() -> None:
    """Each RunOutcome renders exactly its SPEC §4 verdict line, leading with `exit N`."""
    expected = {
        RunOutcome.APPLIED_CLEAN: (0, "exit 0 · all renames applied"),
        RunOutcome.DRY_RUN_CLEAN: (0, "exit 0 · dry run — nothing changed"),
        RunOutcome.DECLINED: (0, "exit 0 · not applied — nothing changed"),
        RunOutcome.NO_MATCHES: (0, "exit 0 · no files to rename"),
        RunOutcome.APPLIED_WITH_SKIPS: (2, "exit 2 · completed with skips — review and re-run"),
        RunOutcome.DRY_RUN_CONFLICTS: (
            2,
            "exit 2 · dry run — conflicts flagged; resolve before applying",
        ),
        RunOutcome.MYLIST_FAILED: (2, "exit 2 · renames applied; some MyList updates failed"),
        RunOutcome.CONFLICT_FAIL_ABORT: (
            1,
            "exit 1 · aborted on destination conflicts (--on-conflict=fail)",
        ),
        RunOutcome.INTERRUPTED: (130, "exit 130 · interrupted"),
    }
    assert set(expected) == set(RunOutcome)  # the table is exhaustive
    for outcome, (code, verdict) in expected.items():
        assert outcome.exit_code == code
        assert outcome.verdict == verdict


def test_footer_recaps_factored_root_line() -> None:
    """The footer opens with the same `→` value the plan header carried (SPEC §3)."""
    console, buf = _plain_console()
    ledger = Ledger(console)
    items = [RenameItem(_abs("in", "a.mkv"), _abs("media", "Show", "a.mkv"), kind=RenameKind.FILE)]
    ledger.plan(items)
    ledger.apply(renamed=1, dest_exists=0, source_missing=0)
    ledger.footer(RunOutcome.APPLIED_CLEAN)
    lines = buf.getvalue().splitlines()
    first_rule = lines.index(RULE)
    assert lines[first_rule + 1] == f"  → {_abs('media', 'Show')}"


def test_footer_dry_run_action_row_with_flagged(tmp_path: Path) -> None:
    """Dry-run footer carries the plan action row with the flagged reason (SPEC §3)."""
    console, buf = _plain_console()
    ledger = Ledger(console)
    occupied = tmp_path / "occupied.mkv"
    occupied.write_bytes(b"x")
    items = [
        RenameItem(str(tmp_path / "a.mkv"), str(tmp_path / "out" / "a.mkv"), kind=RenameKind.FILE),
        RenameItem(str(tmp_path / "b.mkv"), str(tmp_path / "out" / "b.mkv"), kind=RenameKind.FILE),
        RenameItem(str(tmp_path / "c.mkv"), str(occupied), kind=RenameKind.FILE),
    ]
    ledger.plan(items, conflict_indexes=frozenset({2}), dry_run=True)
    ledger.footer(RunOutcome.DRY_RUN_CONFLICTS)
    out = buf.getvalue()
    assert "  plan        2 to rename · 1 flagged (destination exists)" in out
    assert out.splitlines()[-1] == "  exit 2 · dry run — conflicts flagged; resolve before applying"


def test_footer_declined_action_row() -> None:
    """A declined confirm gate renders `not applied (declined)` (SPEC §3)."""
    console, buf = _plain_console()
    ledger = Ledger(console)
    items = [RenameItem(_abs("in", "a.mkv"), _abs("media", "Show", "a.mkv"), kind=RenameKind.FILE)]
    ledger.plan(items)
    ledger.declined()
    ledger.footer(RunOutcome.DECLINED)
    out = buf.getvalue()
    assert "  plan        1 to rename · not applied (declined)" in out
    assert out.splitlines()[-1] == "  exit 0 · not applied — nothing changed"


def test_footer_trivial_path_omits_unused_rows() -> None:
    """Trivial no-work paths footer with only the settled rows — omitted, not zero-padded."""
    console, buf = _plain_console()
    ledger = Ledger(console)
    ledger.discover(0)
    ledger.footer(RunOutcome.NO_MATCHES)
    lines = buf.getvalue().splitlines()
    assert lines == [
        "  discover    0 found",
        RULE,
        "  discover    0 found",
        RULE,
        "  exit 0 · no files to rename",
    ]


def test_verdict_only_prints_single_degraded_line() -> None:
    """A post-plan fatal abort degrades to the verdict line only — no rule, no rows."""
    console, buf = _plain_console()
    ledger = Ledger(console)
    ledger.discover(3)  # rows exist but must not print
    buf.truncate(0)
    buf.seek(0)
    ledger.verdict_only(RunOutcome.CONFLICT_FAIL_ABORT)
    assert buf.getvalue() == "  exit 1 · aborted on destination conflicts (--on-conflict=fail)\n"


def test_parity_tty_output_equals_plain_after_ansi_strip(tmp_path: Path) -> None:
    """TTY output equals non-TTY output byte-for-byte after ANSI stripping, at any width."""
    import re as _re

    def _run(console: Console) -> None:
        ledger = Ledger(console)
        ledger.discover(3)
        ledger.hash_lookup(cached=1, fetched=1, no_match=1)
        ledger.plan(
            [
                RenameItem(
                    _abs("in", "a [GRP].mkv"),
                    _abs("media", "Show", "Show - 01.mkv"),
                    kind=RenameKind.FILE,
                ),
                RenameItem(_abs("in", "b.mkv"), "(AniDB lookup failed)", kind=RenameKind.SKIP),
            ],
            folder_conflicts=["two source folders map to Show — merge"],
        )
        ledger.apply(renamed=1, dest_exists=1, source_missing=0)
        ledger.mylist(1)
        ledger.footer(RunOutcome.APPLIED_WITH_SKIPS)

    plain_buf = io.StringIO()
    _run(Console(file=plain_buf, no_color=True, width=40))
    tty_buf = io.StringIO()
    _run(Console(file=tty_buf, force_terminal=True, color_system="truecolor", width=200))
    stripped = _re.sub(r"\x1b\[[0-9;]*m", "", tty_buf.getvalue())
    assert stripped == plain_buf.getvalue()


def test_footer_redaction_carries_one_path_and_no_names() -> None:
    """SPEC §7: the footer carries at most the factored root — never filenames or titles."""
    console, buf = _plain_console()
    ledger = Ledger(console)
    items = [
        RenameItem(
            _abs("in", "SENTINEL-SOURCE [GRP].mkv"),
            _abs("media", "Lib", "SentinelSeriesA", "SENTINEL-EP - 01.mkv"),
            kind=RenameKind.FILE,
        ),
        RenameItem(
            _abs("in", "other.mkv"),
            _abs("media", "Lib", "SentinelSeriesB", "OTHER-EP - 01.mkv"),
            kind=RenameKind.FILE,
        ),
        RenameItem(
            _abs("in", "SENTINEL-NOMATCH.mkv"), "(AniDB lookup failed)", kind=RenameKind.SKIP
        ),
    ]
    ledger.plan(items)
    ledger.apply(renamed=1, dest_exists=1, source_missing=0)
    ledger.footer(RunOutcome.APPLIED_WITH_SKIPS)
    out = buf.getvalue()
    footer_part = out[out.index(RULE) :]
    assert f"  → {_abs('media', 'Lib')}" in footer_part  # the one allowed path
    for sentinel in ("SENTINEL-SOURCE", "SENTINEL-EP", "SENTINEL-NOMATCH", "SentinelSeries"):
        assert sentinel not in footer_part


def test_streamed_counter_rows_are_byte_identical_to_footer_rows() -> None:
    """Superset identity (SPEC §3): the streamed counter line IS the footer's row."""
    console, buf = _plain_console()
    ledger = Ledger(console)
    ledger.discover(13)
    ledger.hash_lookup(cached=8, fetched=4, no_match=1)
    ledger.footer(RunOutcome.APPLIED_CLEAN)
    lines = buf.getvalue().splitlines()
    assert lines[2] == RULE  # the rule opens the footer block
    assert lines[3] == lines[0]  # footer discover row == streamed line (equality, not similarity)
    assert lines[4] == lines[1]  # footer hash+look row == streamed line
    assert lines[5] == RULE
    assert lines[6] == "  exit 0 · all renames applied"
