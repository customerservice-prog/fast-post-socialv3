# FastPost — no third-party API keys

This project is designed to run **without OpenAI, Meta Graph API tokens, or any other paid cloud API keys**.

## What runs locally

| Area | How it works |
|------|----------------|
| **Captions** | `backend/ai_generator.py` — templates + your crawled site text |
| **Crawl** | `backend/crawler.py` — HTTP + BeautifulSoup |
| **Queue / DB** | SQLite on disk |
| **Posting** | Playwright opens Chromium like a normal user; you sign in to Facebook/Instagram in that session (cookies live under `PROFILES_DIR`) |

## Optional environment variables

Nothing here is an “API key.” See **`.env.example`** for tuning (timeouts, paths, Flask `SECRET_KEY`, Playwright paths, Facebook wait knobs).

## Social login ≠ API key

You still log in to Meta **in the browser** the way a human would. This repo does **not** embed Meta app IDs, client secrets, or long-lived page tokens for the Graph API.

## Verify the codebase

From the repo root, search for common vendor SDK patterns (should only hit this doc / comments):

```bash
grep -RInE "openai|anthropic|OPENAI_API_KEY" backend frontend --exclude-dir=__pycache__
```

(Adjust if you intentionally add official Graph API support later.)
