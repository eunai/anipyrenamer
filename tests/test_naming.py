"""Tests for naming template and sanitization."""
from __future__ import annotations

import pytest

from anipyrenamer.naming import DEFAULT_TEMPLATE, render_template


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


def test_default_template_constant() -> None:
    assert "%title%" in DEFAULT_TEMPLATE
    assert "%epno%" in DEFAULT_TEMPLATE
    assert "%ext%" in DEFAULT_TEMPLATE


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
