# FastPost Social v3 — AI Social Media Marketing Bot

An intelligent, no-third-party-AI social media automation platform. Scrapes your business website, generates captions locally from that content, and posts to Facebook/Instagram with stealth human-like behavior.

## Project Structure
- backend/app.py — Flask REST API server
- backend/crawler.py — Business website scraper  
- backend/ai_generator.py — Local template-based content generator
- backend/scheduler.py — 3x/day post scheduler
- backend/stealth_poster.py — Playwright stealth automation
- backend/database.py — SQLite handler
- frontend/index.html — Dashboard UI
- frontend/style.css — Dashboard styles
- frontend/app.js — Dashboard logic

## Features
- AI generates 3 daily posts from your business website
- Daily Queue Dashboard to review before posting
- Human-in-the-Loop posting (you approve, stealth browser posts)
- Anti-detection: playwright-stealth, random timing, human mouse curves
- Multi-account support (Facebook, Instagram)
- Engagement learning to improve posts over time
- SaaS-ready architecture

## Tech Stack
- Frontend: HTML5, CSS3, Vanilla JS
- Backend: Python 3.11, Flask
- Content: local templates + crawl data (no API keys)
- Automation: Playwright + playwright-stealth
- Scraping: BeautifulSoup4
- Scheduling: APScheduler
- Database: SQLite

## Setup
```bash
git clone https://github.com/customerservice-prog/fast-post-socialv3.git
cd fast-post-socialv3/backend
pip install -r requirements.txt
playwright install chromium
```

Optional `.env` in `/backend` (for production, set a strong `SECRET_KEY`):
```
SECRET_KEY=your_secret
```

Run: `python app.py` from `/backend`, then open **http://localhost:5000** (the server serves the dashboard).

## Deploy (Render / Railpack)

The repo root includes **`requirements.txt`** (includes `backend/requirements.txt`), **`start.sh`**, and **`render.yaml`** so hosts can detect Python and how to start the app.

- **Build command** (if not using `render.yaml`):  
  `pip install -r requirements.txt && cd backend && python -m playwright install chromium`
- **Start command**: `sh start.sh` (runs `backend/app.py`; honors **`PORT`** and **`FLASK_DEBUG=0`** on the server).

**Playwright/Chromium** on cloud hosts may need extra OS libraries; if the build fails on browser install, use a Dockerfile or your platform’s Playwright docs. Stealth posting is optional for health checks — the API and dashboard still run.

## License
MIT
