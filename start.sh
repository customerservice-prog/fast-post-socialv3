#!/usr/bin/env sh
# Railpack / Render: production WSGI (Gunicorn). Local dev: python app.py from backend/
#
# Chromium needs system libs (e.g. libglib) that slim runtimes omit. Build may install
# --with-deps, but the final deploy image often does not carry those packages — install here.
set -e
ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$ROOT/.playwright-browsers}"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
cd "$ROOT/backend"
export DEBIAN_FRONTEND=noninteractive
python -m playwright install --with-deps chromium
export PYTHONUNBUFFERED=1
PORT="${PORT:-5000}"
TIMEOUT="${GUNICORN_TIMEOUT:-${POST_TIMEOUT_SECONDS:-1200}}"
exec gunicorn -w 1 -k gthread --threads 4 \
  -b "0.0.0.0:${PORT}" \
  --timeout "$TIMEOUT" \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile - \
  wsgi:application
