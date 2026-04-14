#!/usr/bin/env sh
# Railpack / Render: entrypoint for the web service (Linux).
set -e
ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd "$ROOT/backend"
export PYTHONUNBUFFERED=1
exec python app.py
