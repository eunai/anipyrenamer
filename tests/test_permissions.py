from __future__ import annotations

import os
import stat
import sys
import warnings
from pathlib import Path

import pytest

from anipyrenamer.permissions import (
    ensure_owner_only,
    warn_if_shared_directory_windows,
    warn_if_world_readable,
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits not meaningful on Windows")
def test_ensure_owner_only_sets_0600(tmp_path: Path) -> None:
    p = tmp_path / "secret.txt"
    p.write_text("x", encoding="utf-8")
    os.chmod(p, 0o644)
    ensure_owner_only(p)
    mode = stat.S_IMODE(os.stat(p).st_mode)
    assert mode == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits not meaningful on Windows")
def test_warn_if_world_readable_emits_warning(tmp_path: Path) -> None:
    p = tmp_path / "secret.txt"
    p.write_text("x", encoding="utf-8")
    os.chmod(p, 0o644)
    with pytest.warns(UserWarning, match=r"chmod 600"):
        warn_if_world_readable(p)


def test_windows_shared_dir_warning_is_noop_on_non_windows(tmp_path: Path) -> None:
    # Should never warn on non-Windows; on Windows this path may or may not warn depending on env.
    if sys.platform != "win32":
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_if_shared_directory_windows(tmp_path / "secret.txt")
        assert w == []
