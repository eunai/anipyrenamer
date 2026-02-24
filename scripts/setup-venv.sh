#!/usr/bin/env bash
set -e
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
cd "$SCRIPT_DIR/.."
if [ -d .venv ]; then
    echo ".venv already exists. Activate it and run scripts/install-dev.sh if needed."
    exit 0
fi
python3.13 -m venv .venv
# shellcheck source=/dev/null
. .venv/bin/activate
pip install -e ".[dev]"
echo "Created .venv and installed anipyrenamer[dev]. Activate with: source .venv/bin/activate"
