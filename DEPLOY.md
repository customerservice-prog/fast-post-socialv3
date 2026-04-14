# FastPost — saved deploy reference

Pinned configuration in this repo (commit and redeploy from **`main`**):

| File | Role |
|------|------|
| **`railway.json`** | Railway / Railpack: `buildCommand` → `sh build.sh`, `startCommand` → `sh start.sh`, health → **`GET /api/health`** |
| **`build.sh`** | `pip install -r requirements.txt` + `playwright install --with-deps chromium` (uses **`PLAYWRIGHT_BROWSERS_PATH`** when set) |
| **`start.sh`** | `playwright install --with-deps chromium` at boot + **Gunicorn** → `backend/wsgi.py` (uses **`PORT`**) |
| **`Procfile`** | `web: sh start.sh` (fallback for platforms that read Procfile) |
| **`render.yaml`** | Optional Render Blueprint only (not used by Railway) |
| **`requirements.txt`** | Python deps at repo root (Railpack install layer) |

## Railway (recommended)

1. Connect this GitHub repo; Railpack picks up **`railway.json`**.
2. **Variables** (service → Variables):

| Variable | Value |
|----------|--------|
| `PLAYWRIGHT_BROWSERS_PATH` | `/app/.playwright-browsers` |
| `FLASK_DEBUG` | `0` |
| `FB_HEADED` | `0` or unset |
| `DATABASE_PATH` | `/data/fastpost.db` (with volume) |
| `PROFILES_DIR` | `/data/browser_profiles` (with volume) |

3. **Volume:** mount at **`/data`** and set **`DATABASE_PATH=/data/fastpost.db`**. Without this, SQLite lives on ephemeral disk and **accounts disappear on every redeploy** (the app will log the DB path at startup).
4. **Posting:** runs **headless** on the server — no browser window on your PC (see Settings in the app).

## Smoke checks (after deploy)

- `GET https://<your-host>/api/health` → JSON with `"status":"ok"` and `"posting_headless": true` on cloud.
- `GET https://<your-host>/api/dashboard` → includes `"posting_headless"`.

Local (from clone):

```bash
python scripts/verify_api_posting_flags.py
python scripts/verify_playwright_headless.py
```

## Optional

- **`SECRET_KEY`** — Flask cookie signing only; not an API key.
- See **`.env.example`** and **`README.md`** for full context.
