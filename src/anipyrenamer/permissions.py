"""Platform-conditional file permission checks (SEC-09).

This module provides best-effort helpers:
- Unix: warn when sensitive files are group/other-readable; chmod cache DB to 0o600.
- Windows: no ACL mutation (best-effort heuristic warning only).
"""

from __future__ import annotations

import ntpath
import os
import stat
import sys
import warnings
from pathlib import Path


def ensure_owner_only(path: str | Path) -> None:
    """Best-effort: set Unix mode to owner read/write only (0o600).

    On Windows, this is a no-op (NTFS ACLs are not modified).
    """

    if sys.platform == "win32":
        return
    p = Path(path)
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Best-effort only: filesystems or permissions may reject chmod.
        return


def warn_if_world_readable(path: str | Path) -> None:
    """Warn on Unix when file is readable by group or other users."""

    if sys.platform == "win32":
        return
    p = Path(path)
    try:
        st = os.stat(p)
    except OSError:
        return
    if st.st_mode & (stat.S_IRGRP | stat.S_IROTH):
        warnings.warn(
            f"{p} is readable by other users. Recommended: chmod 600 {p}",
            stacklevel=2,
        )


def warn_if_shared_directory_windows(path: str | Path) -> None:
    """Windows-only heuristic warning for sensitive files outside user profile dirs."""

    if sys.platform != "win32":
        return
    resolved = ntpath.abspath(str(path))
    user_profile = os.environ.get("USERPROFILE", "")
    appdata = os.environ.get("APPDATA", "")
    if user_profile and _is_within_directory(resolved, user_profile):
        return
    if appdata and _is_within_directory(resolved, appdata):
        return
    warnings.warn(
        f"{path} is not under the user profile directory. Ensure NTFS ACLs restrict access.",
        stacklevel=2,
    )


def _is_within_directory(path: str | Path, directory: str | Path) -> bool:
    """Return whether a path is under a directory using Windows path semantics."""
    normalized_path = ntpath.normcase(ntpath.abspath(str(path)))
    normalized_directory = ntpath.normcase(ntpath.abspath(str(directory)))
    try:
        return ntpath.commonpath((normalized_path, normalized_directory)) == normalized_directory
    except ValueError:
        # Different Windows drives (or malformed paths) are never contained.
        return False
