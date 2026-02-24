"""Tests for naming template and sanitization."""
from __future__ import annotations

import pytest

from anipyrenamer.naming import (
    DEFAULT_FILE_TEMPLATE,
    DEFAULT_FOLDER_TEMPLATE,
    render_template,
)


def test_render_template_default_tokens() -> None:
    out = render_template(
        "%title% - %epno% - %eptitle% [%group%]%ext%",
        title="Crest of the Stars",
        epno="01",
        eptitle="Invasion",
        group="Group",
        extension=".mkv",
    )
    assert "Crest" in out and "01" in out and "Invasion" in out and "Group" in out
    assert out.endswith(".mkv")


def test_sanitize_removes_illegal_chars() -> None:
    out = render_template("%title%", title="Test: File/Name*")
    assert ":" not in out and "/" not in out and "*" not in out


def test_sanitize_preserves_spaces() -> None:
    """Spaces are preserved in output; only truly illegal chars replaced."""
    out = render_template("%title% - %eptitle%", title="Solo Leveling", eptitle="I'm Used to It")
    assert "Solo Leveling" in out
    assert "I'm Used to It" in out
    assert "  " not in out  # multiple spaces collapsed to one


def test_default_file_template_constant() -> None:
    assert "%title%" in DEFAULT_FILE_TEMPLATE
    assert "%epno%" in DEFAULT_FILE_TEMPLATE
    assert "%ext%" in DEFAULT_FILE_TEMPLATE


def test_default_folder_template_constant() -> None:
    assert "%title%" in DEFAULT_FOLDER_TEMPLATE
    assert "%group%" in DEFAULT_FOLDER_TEMPLATE
    assert "%ext%" in DEFAULT_FOLDER_TEMPLATE


def test_render_template_folder_extension_empty() -> None:
    """Folder names use extension='' so %ext% is empty."""
    out = render_template(
        DEFAULT_FOLDER_TEMPLATE,
        title="My Anime",
        group="Subs",
        extension="",
    )
    assert "My Anime" in out or "My-Anime" in out
    assert "Subs" in out
    assert not out.endswith(".")


def test_render_template_aid_eid_fid_gid() -> None:
    out = render_template(
        "%title% %aid% %eid% %fid% %gid%",
        title="X",
        aid="1",
        eid="2",
        fid="3",
        gid="4",
    )
    assert "1" in out and "2" in out and "3" in out and "4" in out


def test_render_template_plex_folder_with_anidb_id() -> None:
    """Plex-style folder template renders [anidb-<aid>] correctly."""
    out = render_template(
        "%title% [%group%] [anidb-%aid%]",
        title="My Anime",
        group="ABC",
        aid="12345",
        extension="",
    )
    assert "My Anime" in out
    assert "[ABC]" in out
    assert "[anidb-12345]" in out


def test_render_template_group_short_and_long() -> None:
    """%group% uses short by default; %grouplong% uses long."""
    out = render_template(
        "[%group%] [%grouplong%]",
        group="SEV",
        group_long="Sublime Encoded Video",
    )
    assert "SEV" in out and "Sublime" in out
    out_short_only = render_template("[%group%]", group="SEV", group_long="")
    assert "SEV" in out_short_only
