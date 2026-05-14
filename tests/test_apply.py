"""Tests for preview and apply."""

from __future__ import annotations
from pathlib import Path

import pytest
from rich.console import Console

from anipyrenamer.apply import _plan_sort_key, apply_plan, preview_plan
from anipyrenamer.models import RenameItem, RenameKind


def _preview_text(items: list[RenameItem], *, styles: bool = False) -> str:
    console = Console(record=True, force_terminal=True, color_system="truecolor", width=200)
    preview_plan(items, console)
    return console.export_text(styles=styles)


def test_preview_plan_no_crash() -> None:
    items = [RenameItem("/media/Old/x.mkv", "/media/New/y.mkv")]
    output = _preview_text(items)
    assert "Folders" in output
    assert "Files" in output


def test_preview_plan_with_skip_item_no_crash() -> None:
    """Preview includes SKIP items (AniDB lookup failed) without crashing."""
    items = [
        RenameItem("/path/to/video.mkv", "(AniDB lookup failed)", kind=RenameKind.SKIP),
    ]
    output = _preview_text(items)
    assert "Files (skipped)" in output
    assert "(AniDB lookup failed)" in output


def test_preview_plan_empty_input_prints_nothing() -> None:
    assert _preview_text([]) == ""


def test_preview_plan_renders_three_sections_when_all_present() -> None:
    items = [
        RenameItem("/media/Old/ep01.mkv", "/media/New/Show 01.mkv"),
        RenameItem("/media/Same/ep02.mkv", "/media/Same/Show 02.mkv"),
        RenameItem("/media/Failed/ep03.mkv", "(AniDB lookup failed)", kind=RenameKind.SKIP),
    ]
    output = _preview_text(items)
    assert output.index("Folders") < output.index("Files") < output.index("Files (skipped)")


def test_preview_plan_omits_folders_section_when_all_in_place() -> None:
    output = _preview_text([RenameItem("/media/Same/ep01.mkv", "/media/Same/Show 01.mkv")])
    assert "Folders" not in output
    assert "Files" in output


def test_preview_plan_omits_files_section_when_only_skip_items() -> None:
    output = _preview_text(
        [RenameItem("/media/Failed/ep01.mkv", "(AniDB lookup failed)", kind=RenameKind.SKIP)]
    )
    assert "Folders" not in output
    assert "Files (skipped)" in output
    assert "│ Files " not in output


def test_preview_plan_factors_plan_root_line() -> None:
    output = _preview_text(
        [
            RenameItem("/media/animu/Old/ep01.mkv", "/media/animu/New/Show 01.mkv"),
            RenameItem("/media/animu/Old/ep02.mkv", "/media/animu/New/Show 02.mkv"),
        ]
    )
    assert "/media/animu" in output
    assert "/media/animu/Old/ep01.mkv" not in output


def test_preview_plan_multi_filesystem_root_renders_two_plan_root_lines() -> None:
    output = _preview_text(
        [
            RenameItem(r"D:\media\Old\ep01.mkv", r"D:\media\New\Show 01.mkv"),
            RenameItem(r"K:\animu\Old\ep01.mkv", r"K:\animu\New\Movie 01.mkv"),
        ]
    )
    assert r"D:\media" in output
    assert r"K:\animu" in output


def test_preview_plan_folders_table_dedupes_by_old_new_parent_pair() -> None:
    output = _preview_text(
        [
            RenameItem("/media/Old/ep01.mkv", "/media/New/Show 01.mkv"),
            RenameItem("/media/Old/ep02.mkv", "/media/New/Show 02.mkv"),
        ]
    )
    assert output.count("Old/") == 2  # one Folders row + one Files header row
    assert output.count("New/") == 2


def test_preview_plan_folders_table_omits_in_place_renames() -> None:
    output = _preview_text(
        [
            RenameItem("/media/Same/ep01.mkv", "/media/Same/Show 01.mkv"),
            RenameItem("/media/Old/ep02.mkv", "/media/New/Show 02.mkv"),
        ]
    )
    assert "Same/" not in output.split("Files", 1)[0]
    assert "Old/" in output.split("Files", 1)[0]


def test_preview_plan_files_tree_renders_branches() -> None:
    output = _preview_text(
        [
            RenameItem("/media/Old/ep01.mkv", "/media/New/Show 01.mkv"),
            RenameItem("/media/Old/ep02.mkv", "/media/New/Show 02.mkv"),
        ]
    )
    assert "├─ ep01.mkv" in output
    assert "└─ ep02.mkv" in output
    assert "├─ Show 01.mkv" in output
    assert "└─ Show 02.mkv" in output


def test_preview_plan_files_tree_loose_file_has_no_folder_header() -> None:
    output = _preview_text([RenameItem("/media/old.mkv", "/media/new.mkv")])
    assert "old.mkv" in output
    assert "new.mkv" in output
    assert "old/" not in output
    assert "new/" not in output


def test_preview_plan_files_tree_in_place_rename_shows_same_folder_both_sides() -> None:
    output = _preview_text(
        [
            RenameItem("/media/Same/ep01.mkv", "/media/Same/Show 01.mkv"),
            RenameItem("/media/Other/ep02.mkv", "/media/Other/Show 02.mkv"),
        ]
    )
    assert output.count("Same/") == 2


def test_preview_plan_skip_section_renders_anidb_lookup_failed_literal() -> None:
    output = _preview_text(
        [RenameItem("/media/Failed/ep01.mkv", "(AniDB lookup failed)", kind=RenameKind.SKIP)]
    )
    assert "└─ (AniDB lookup failed)" in output


def test_preview_plan_segment_diff_colors_diverging_suffix() -> None:
    output = _preview_text(
        [RenameItem("/media/Old/ep01.mkv", "/media/New/Show 01.mkv")], styles=True
    )
    assert "\x1b[32mNew" in output
    assert "32mShow 01.mkv" in output


def test_preview_plan_skip_section_uses_no_green_styling() -> None:
    output = _preview_text(
        [RenameItem("/media/Failed/ep01.mkv", "(AniDB lookup failed)", kind=RenameKind.SKIP)],
        styles=True,
    )
    assert "\x1b[32m" not in output


def test_preview_plan_cross_drive_row_renders_absolute_current() -> None:
    output = _preview_text([RenameItem(r"D:\source\Old\ep01.mkv", r"K:\dest\New\Show 01.mkv")])
    assert "D:\\source\\Old\\" in output
    assert r"K:\dest\New\Show 01.mkv" not in output
    assert r"K:\dest" in output


def test_preview_plan_folders_sorted_by_new_parent_casefold() -> None:
    output = _preview_text(
        [
            RenameItem("/media/old-b/ep01.mkv", "/media/Bbb/Show 01.mkv"),
            RenameItem("/media/old-a/ep01.mkv", "/media/aaa/Show 01.mkv"),
        ]
    )
    assert output.index("aaa/") < output.index("Bbb/")


def test_preview_plan_files_within_folder_sorted_by_episode() -> None:
    output = _preview_text(
        [
            RenameItem("/media/Old/ep03.mkv", "/media/New/Show ep03.mkv"),
            RenameItem("/media/Old/ep01.mkv", "/media/New/Show ep01.mkv"),
            RenameItem("/media/Old/ep02.mkv", "/media/New/Show ep02.mkv"),
        ]
    )
    assert output.index("Show ep01.mkv") < output.index("Show ep02.mkv")
    assert output.index("Show ep02.mkv") < output.index("Show ep03.mkv")


def test_preview_plan_type_from_first_file_in_bucket() -> None:
    output = _preview_text(
        [
            RenameItem("/media/Old/ep01.mkv", "/media/New/Show 01.mkv", anime_type="tv"),
            RenameItem("/media/Old/ep02.mkv", "/media/New/Show 02.mkv", anime_type="movie"),
        ]
    )
    assert " tv" in output


def test_preview_plan_type_empty_renders_em_dash() -> None:
    output = _preview_text([RenameItem("/media/Old/ep01.mkv", "/media/New/Show 01.mkv")])
    assert "—" in output


def test_preview_plan_sorted_by_folder_then_episode() -> None:
    """Preview table order is by destination folder (case-insensitive) then episode number."""
    items = [
        RenameItem("/any/222222.mkv", "/anime/Dan Da Dan [Subs]/Dan Da Dan 01 - First [Subs].mkv"),
        RenameItem("/any/11.mkv", "/anime/Blue Lock [SEV]/Blue Lock 02 - Monster [SEV].mkv"),
        RenameItem("/any/other.mkv", "/anime/Blue Lock [SEV]/Blue Lock 01 - Dream [SEV].mkv"),
        RenameItem("/any/x.mkv", "/anime/Nana [EMBER]/Nana 01 - Prologue [EMBER].mkv"),
    ]
    ordered = sorted(items, key=_plan_sort_key)
    new_paths = [item.new_path for item in ordered]
    # Blue Lock (01 then 02), then Dan Da Dan 01, then Nana 01
    assert "Blue Lock 01" in new_paths[0]
    assert "Blue Lock 02" in new_paths[1]
    assert "Dan Da Dan 01" in new_paths[2]
    assert "Nana 01" in new_paths[3]


def test_apply_plan_dry_run_does_nothing(tmp_path: Path) -> None:
    src = tmp_path / "orig.mkv"
    src.write_bytes(b"data")
    items = [RenameItem(str(src), str(tmp_path / "new.mkv"))]
    apply_plan(items, str(tmp_path / "db.sqlite"), dry_run=True)
    assert src.exists()
    assert not (tmp_path / "new.mkv").exists()


def test_apply_plan_moves_file(tmp_path: Path) -> None:
    src = tmp_path / "orig.mkv"
    src.write_bytes(b"data")
    db = tmp_path / "cache.sqlite"
    from anipyrenamer.cache import init_db

    init_db(str(db))
    items = [RenameItem(str(src), str(tmp_path / "new.mkv"))]
    apply_plan(items, str(db), dry_run=False)
    assert not src.exists()
    assert (tmp_path / "new.mkv").exists()
    assert (tmp_path / "new.mkv").read_bytes() == b"data"


def test_apply_plan_moves_files_and_removes_empty_source_dir(tmp_path: Path) -> None:
    """Apply moves files to target dir (creating it) and removes empty source dir."""
    from anipyrenamer.cache import init_db

    old_dir = tmp_path / "OldDir"
    old_dir.mkdir()
    file_path = old_dir / "ep.mkv"
    file_path.write_bytes(b"video")
    new_dir = tmp_path / "NewDir"
    new_file_name = "renamed.mkv"
    db = tmp_path / "cache.sqlite"
    init_db(str(db))
    items = [
        RenameItem(str(file_path), str(new_dir / new_file_name), kind=RenameKind.FILE),
    ]
    apply_plan(items, str(db), dry_run=False)
    assert not file_path.exists()
    assert not old_dir.exists()
    assert new_dir.is_dir()
    assert (new_dir / new_file_name).exists()
    assert (new_dir / new_file_name).read_bytes() == b"video"


def test_apply_plan_ignores_skip_kind(tmp_path: Path) -> None:
    """SKIP items are not moved; only FILE items are applied."""
    src = tmp_path / "video.mkv"
    src.write_bytes(b"data")
    db = tmp_path / "cache.sqlite"
    from anipyrenamer.cache import init_db

    init_db(str(db))
    items = [
        RenameItem(str(src), "(AniDB lookup failed)", kind=RenameKind.SKIP),
    ]
    apply_plan(items, str(db), dry_run=False)
    assert src.exists()
    assert src.read_bytes() == b"data"


def test_apply_plan_skips_when_destination_exists(tmp_path: Path) -> None:
    """When destination already exists (and is not source), apply skips that item (no overwrite)."""
    from anipyrenamer.cache import init_db

    src = tmp_path / "orig.mkv"
    src.write_bytes(b"original")
    existing = tmp_path / "existing.mkv"
    existing.write_bytes(b"existing")
    db = tmp_path / "cache.sqlite"
    init_db(str(db))
    items = [RenameItem(str(src), str(existing), kind=RenameKind.FILE)]
    apply_plan(items, str(db), dry_run=False)
    assert src.exists()
    assert existing.read_bytes() == b"existing"


def test_apply_plan_chdir_parent_when_cwd_matches_source_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When cwd is the emptied source dir, apply moves cwd to parent before removal."""
    from anipyrenamer.cache import init_db

    old_dir = tmp_path / "OldDir"
    old_dir.mkdir()
    src = old_dir / "video.mkv"
    src.write_bytes(b"data")
    new_dir = tmp_path / "NewDir"
    db = tmp_path / "cache.sqlite"
    init_db(str(db))
    monkeypatch.chdir(old_dir)

    items = [RenameItem(str(src), str(new_dir / "video.mkv"), kind=RenameKind.FILE)]
    apply_plan(items, str(db), dry_run=False)

    assert not old_dir.exists()
    assert Path.cwd() == tmp_path
    assert (new_dir / "video.mkv").exists()


def test_apply_plan_cleanup_permission_error_is_non_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Locked/permission errors during source-dir cleanup are logged and do not abort apply."""
    from anipyrenamer.cache import init_db

    old_dir = tmp_path / "OldDir"
    old_dir.mkdir()
    src = old_dir / "video.mkv"
    src.write_bytes(b"data")
    new_dir = tmp_path / "NewDir"
    db = tmp_path / "cache.sqlite"
    init_db(str(db))

    original_rmdir = Path.rmdir

    def _patched_rmdir(path: Path) -> None:
        if path.resolve() == old_dir.resolve():
            raise PermissionError("[WinError 32] simulated lock")
        original_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", _patched_rmdir)

    items = [RenameItem(str(src), str(new_dir / "video.mkv"), kind=RenameKind.FILE)]
    apply_plan(items, str(db), dry_run=False)

    assert old_dir.exists()
    assert (new_dir / "video.mkv").exists()
    assert any("Skipping source directory cleanup" in rec.message for rec in caplog.records)
