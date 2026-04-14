# FastPost Social v3 — AI Social Media Marketing Bot

An intelligent, **no–API-key** social media stack: no OpenAI, no cloud LLM billing, no vendor tokens. It scrapes your business website, generates captions **locally** from that content, and posts to Facebook/Instagram with stealth human-like behavior (your normal browser login — no platform API keys in this app).

See **[NO_API_KEYS.md](NO_API_KEYS.md)** for a short checklist and **`.env.example`** for optional tuning (not API keys).

## Project Structure
- backend/app.py — Flask REST API server
- backend/crawler.py — Business website scraper  
- backend/ai_generator.py — Local template-based content generator
- backend/scheduler.py — Daily draft scheduler
- backend/stealth_poster.py — Playwright stealth automation
- backend/database.py — SQLite handler
- backend/wsgi.py — Gunicorn entry (production)
- frontend/index.html — Dashboard UI
- frontend/style.css — Dashboard styles
- frontend/app.js — Dashboard logic
- `Makefile` — quick targets on macOS / Linux / Git Bash (`make help`)
- `scripts/dev.ps1` — same idea on Windows PowerShell

## Features
- Builds **three draft posts per account per day** from crawled website content (local templates, no cloud LLM)
- Daily Queue dashboard to review and edit before posting
- Human-in-the-loop posting (you trigger **Post now**; stealth browser runs headless on servers)
- Anti-detection helpers: playwright-stealth, jittered timing, human-like interaction patterns
- Multi-account support (Facebook, Instagram labels; posting uses your logged-in session)
- SQLite + optional disk paths for production persistence
- Deploy-friendly: root `requirements.txt`, `start.sh`, `build.sh`, **`railway.json`** (Railway / Railpack), optional `render.yaml` (Render)

## Tech Stack
- Frontend: HTML5, CSS3, Vanilla JS
- Backend: Python 3.11+, Flask, Gunicorn (production)
- Content: local templates + crawl data (no API keys)
- Automation: Playwright + playwright-stealth
- Scraping: BeautifulSoup4 + requests
- Scheduling: APScheduler
- Database: SQLite

## Setup
```bash
git clone https://github.com/customerservice-prog/fast-post-socialv3.git
cd fast-post-socialv3
pip install -r requirements.txt
python -m playwright install chromium
cd backend && python app.py
```

Open **http://localhost:5000** (Flask serves the dashboard from the repo).

### One-command shortcuts

- **Unix / Git Bash / WSL:** `make help` then `make dev` (or `make install`, `make playwright`).
- **Windows PowerShell:** `.\scripts\dev.ps1 help` then `.\scripts\dev.ps1 dev`.

Optional: copy **`.env.example`** to **`backend/.env`** and adjust. There are **no required** environment variables for captions, crawling, or scheduling. Facebook/Instagram posting uses Playwright with a normal login session, not the Graph API.

## Production (same machine)
From repo root (installs browsers under `.playwright-browsers` if you set `PLAYWRIGHT_BROWSERS_PATH`):
```bash
export PLAYWRIGHT_BROWSERS_PATH="$(pwd)/.playwright-browsers"
sh build.sh   # pip + playwright --with-deps chromium
sh start.sh   # Gunicorn + backend/wsgi.py
```

## Deploy (Railway — recommended)

The repo includes **`railway.json`**: **build** = `sh build.sh`, **start** = `sh start.sh`, health check = **`GET /api/health`**.

1. **New Railway project** → deploy from this GitHub repo (Railpack will detect Python).
2. **Variables** (service → Variables), set at least:
   - **`PLAYWRIGHT_BROWSERS_PATH`** = `/app/.playwright-browsers` (matches `start.sh` + build; keeps Chromium in the image path)
   - **`FLASK_DEBUG`** = `0`
   - **`FB_HEADED`** = `0` (or omit — do **not** set `1` on the server; there is no X server)
3. **Persistence:** add a **volume** mounted e.g. at **`/data`**, then set:
   - **`DATABASE_PATH`** = `/data/fastpost.db`
   - **`PROFILES_DIR`** = `/data/browser_profiles`  
   (Without a volume, SQLite and browser login profiles are lost on redeploy.)

**`PORT`** is injected by Railway; `start.sh` already uses it. Optional: **`SECRET_KEY`** for Flask (not an API key).

**Playwright:** `build.sh` installs deps + Chromium; `start.sh` runs `playwright install --with-deps chromium` at boot so slim runtimes still get system libraries.

**Headless:** The app detects **`RAILWAY_ENVIRONMENT`** / **`CI`** / **`RENDER`** and forces headless Chromium. If you still see X11 errors, remove **`FB_HEADED`** from Variables or set it to **`0`**.

Smoke-test locally: `make verify-playwright` or `python scripts/verify_playwright_headless.py`.

## Deploy (Render or other hosts)

Same **build** / **start** commands as above. Optional **`render.yaml`** is for Render Blueprints only (not used by Railway). Pin **`FB_HEADED=0`** in the host UI if you add variables manually.

## License
MIT
