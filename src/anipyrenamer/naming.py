"""Naming template and filename sanitization."""

from __future__ import annotations

import re

# Characters illegal in Windows filenames; also sanitize for cross-platform.
ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|\s]+')


def _sanitize(s: str) -> str:
    """Safe filename: no \\ / : * ? \" < > |; collapse whitespace; trim trailing dashes."""
    out = ILLEGAL_CHARS.sub("-", s.strip())
    out = re.sub(r"-+", "-", out).strip("-").strip()
    return out or "Unknown"


def render_template(
    template: str,
    *,
    title: str = "",
    epno: str = "",
    eptitle: str = "",
    group: str = "",
    extension: str = "",
    fileversion: str = "",
    aid: str = "",
    eid: str = "",
    fid: str = "",
    gid: str = "",
) -> str:
    """Replace tokens; result sanitized for filenames."""
    out = template
    out = out.replace("%title%", _sanitize(title) or "Unknown")
    out = out.replace("%epno%", epno or "0")
    out = out.replace("%fileversion%", fileversion or "")
    out = out.replace("%eptitle%", _sanitize(eptitle) or "Episode")
    out = out.replace("%group%", _sanitize(group) or "Unknown")
    out = out.replace("%ext%", extension or "")
    out = out.replace("%aid%", aid or "0")
    out = out.replace("%eid%", eid or "0")
    out = out.replace("%fid%", fid or "0")
    out = out.replace("%gid%", gid or "0")
    return _sanitize(out) or "Unknown"


DEFAULT_TEMPLATE = "%title% - %epno%%fileversion% - %eptitle% [%group%]%ext%"
