#!/usr/bin/env sh
# Optional: set as your platform "Build Command" if the UI does not auto-install Playwright browsers.
set -e
ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
pip install -r "$ROOT/requirements.txt"
cd "$ROOT/backend"
python -m playwright install chromium
