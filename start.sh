#!/usr/bin/env sh
# Railpack / Render: production WSGI (Gunicorn). Local dev: python app.py from backend/
set -e
ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd "$ROOT/backend"
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
