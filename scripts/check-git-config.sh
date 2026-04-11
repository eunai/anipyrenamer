#!/usr/bin/env bash
# Check that Git user.name and user.email are configured (required for commits).
# Run from repo root: ./scripts/check-git-config.sh
set -e
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
cd "$SCRIPT_DIR/.."

if ! git rev-parse --is-inside-work-tree &>/dev/null; then
  exit 0
fi

missing=""
name=$(git config user.name 2>/dev/null || true)
email=$(git config user.email 2>/dev/null || true)
[ -z "$name" ] && missing="user.name"
[ -z "$email" ] && missing="${missing:+$missing and }user.email"

if [ -n "$missing" ]; then
  echo "Git is not configured for this repository. Set $missing before committing."
  echo ""
  echo "For this repo only:"
  echo "  git config user.name \"Your Name\""
  echo "  git config user.email \"you@example.com\""
  echo ""
  echo "For all repos (recommended):"
  echo "  git config --global user.name \"Your Name\""
  echo "  git config --global user.email \"you@example.com\""
  exit 1
fi
exit 0
