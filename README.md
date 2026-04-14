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
- Deploy-friendly: root `requirements.txt`, `start.sh`, `build.sh`, `render.yaml`

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

## Deploy (Render / Railpack)

The repo root includes **`requirements.txt`**, **`build.sh`**, **`start.sh`**, and **`render.yaml`**.

- **Build:** `sh build.sh` (or your host’s equivalent: pip + `playwright install --with-deps chromium`)
- **Start:** `sh start.sh` (Gunicorn, honors **`PORT`**, **`FLASK_DEBUG=0`**, **`PLAYWRIGHT_BROWSERS_PATH`**)

**Playwright/Chromium** on cloud hosts may need OS libraries; `start.sh` runs `playwright install --with-deps` so slim images still get shared libs. Use a persistent disk for **`DATABASE_PATH`** and **`PROFILES_DIR`** if you need data to survive restarts.

**Headless / “headed browser without X server”:** Posting always uses **headless** Chromium on Render (and whenever `RENDER`/`CI`/etc. is set). Do **not** set **`FB_HEADED=1`** in production. `render.yaml` pins **`FB_HEADED=0`**. Smoke-test with `make verify-playwright` or `python scripts/verify_playwright_headless.py` after installing Playwright.

## License
MIT
