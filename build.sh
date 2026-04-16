#!/usr/bin/env sh
# Render Blueprint buildCommand / local full install (Linux).
# Browsers MUST live under the repo (e.g. /app/.playwright-browsers): Railpack final
# images often omit /root/.cache from the install step.
#
# Stripe/billing: do not reference STRIPE_* (or other secrets) here. Keep them runtime-only
# on Railway so BuildKit does not require build-time secrets (see DEPLOY.md).
set -e
ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$ROOT/.playwright-browsers}"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
pip install -r requirements.txt
python -m playwright install --with-deps chromium
