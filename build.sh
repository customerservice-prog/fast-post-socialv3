#!/usr/bin/env sh
# Render Blueprint buildCommand / local full install (Linux).
set -e
ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd "$ROOT"
pip install -r requirements.txt
python -m playwright install --with-deps chromium
