"""Minimal CLI entry point: parse args and exit. No AniDB or renaming yet."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    """Parse CLI arguments and exit. Part A: no discovery or rename logic."""
    parser = argparse.ArgumentParser(
        prog="anipyrenamer",
        description="Rename anime files using ED2K hash and AniDB.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="path",
        help="Files or folders to scan (not implemented yet).",
    )
    parser.parse_args()
    sys.exit(0)


if __name__ == "__main__":
    main()
