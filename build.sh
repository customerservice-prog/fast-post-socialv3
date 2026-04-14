#!/usr/bin/env sh
# Render Blueprint buildCommand / local full install (Linux).
# Browsers MUST live under the repo (e.g. /app/.playwright-browsers): Railpack final
# images often omit /root/.cache from the install step.
set -e
ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$ROOT/.playwright-browsers}"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
pip install -r requirements.txt
python -m playwright install --with-deps chromium
