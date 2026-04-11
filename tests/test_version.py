"""Version consistency tests."""

from __future__ import annotations

import re
from pathlib import Path

from anipyrenamer import __version__


def test_package_version_matches_pyproject() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match is not None
    assert match.group(1) == __version__
