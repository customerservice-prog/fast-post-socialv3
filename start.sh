#!/usr/bin/env sh
# Railpack / Render: production WSGI (Gunicorn). Local dev: python app.py from backend/
#
# Chromium + deps are installed in build.sh to PLAYWRIGHT_BROWSERS_PATH — do NOT run
# `playwright install` here: it blocks Gunicorn until finish, often fails apt in slim runtimes,
# and causes Railway healthchecks on /api/health to fail with "service unavailable".
# Optional: set PLAYWRIGHT_RUNTIME_INSTALL=1 to run a quick browser-only install if missing.
set -e
ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$ROOT/.playwright-browsers}"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
cd "$ROOT/backend"
if [ "${PLAYWRIGHT_RUNTIME_INSTALL:-}" = "1" ]; then
  python -m playwright install chromium 2>/dev/null || true
fi
export PYTHONUNBUFFERED=1
PORT="${PORT:-5000}"
TIMEOUT="${GUNICORN_TIMEOUT:-${POST_TIMEOUT_SECONDS:-840}}"
case "$TIMEOUT" in ''|*[!0-9]*) TIMEOUT=840;; esac
exec gunicorn -w 1 -k gthread --threads 4 \
  -b "0.0.0.0:${PORT}" \
  --timeout "$TIMEOUT" \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile - \
  wsgi:application
