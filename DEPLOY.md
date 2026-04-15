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
| `POST_TIMEOUT_SECONDS` | optional; default **840** (under ~15m HTTP limits on Railway). Increase only on hosts that allow longer requests. |
| `FACEBOOK_APP_ID` | [Meta Developers](https://developers.facebook.com/) — **recommended** for automatic posting without Playwright. |
| `FACEBOOK_APP_SECRET` | Same app (keep private). |
| `FACEBOOK_REDIRECT_URI` | Must match **Valid OAuth Redirect URIs** exactly, e.g. `https://YOUR_DOMAIN/api/facebook/oauth/callback` |
| `PUBLIC_APP_URL` | Optional; `https://YOUR_DOMAIN` (redirect after login). If omitted, derived from `FACEBOOK_REDIRECT_URI`. |

3. **Volume:** mount at **`/data`** and set **`DATABASE_PATH=/data/fastpost.db`**. Without this, SQLite lives on ephemeral disk and **accounts disappear on every redeploy** (the app will log the DB path at startup).
4. **Posting:** runs **headless** on the server — no browser window on your PC (see Settings in the app).
5. **Facebook (recommended):** add a Meta app with **Facebook Login**; set the `FACEBOOK_*` variables and the redirect URI above. In the app: **Accounts → Connect Facebook** once per account, then **Post now** uses the **Graph API** (no JSON files, no Chromium for Facebook). Until the app is **Live**, add your Facebook user under the app’s **Roles** so you can authorize.
6. **Fallback:** **Accounts → Session JSON** (Playwright storage) still works for Instagram-only or if you skip the Meta app. Keep **`PROFILES_DIR` on `/data`** if you use it.

### Meta app: “Can’t load URL” / App Domains

If Facebook shows **The domain of this URL isn’t included in the app’s domains**:

1. **[developers.facebook.com](https://developers.facebook.com/)** → your app → **App settings → Basic**
2. **App domains** — add only the hostname, no `https://`, no path:  
   `socialautopost.online`  
   (Use your real Railway/custom domain. For `www`, either add `www.socialautopost.online` as a second domain or use one canonical URL everywhere.)
3. **Add platform → Website** (if missing): **Site URL** = `https://socialautopost.online/` (trailing slash is fine).
4. **Use cases → Authentication and account creation → Facebook Login** → **Settings** (or **Facebook Login → Settings** in the left nav):
   - **Valid OAuth Redirect URIs** must include exactly:  
     `https://socialautopost.online/api/facebook/oauth/callback`
   - Turn **Client OAuth login** and **Web OAuth login** **On** if you see those toggles.
5. Railway **`FACEBOOK_REDIRECT_URI`** must be **character-for-character** the same as that redirect URI (scheme `https`, correct path).
6. Save, wait a minute, try **Connect Facebook** again (hard refresh the app).

**Wrong (causes “Can’t load URL”):** putting a **Facebook profile or Page URL** in Railway, e.g. `https://www.facebook.com/profile.php?id=…` — that is **not** the OAuth callback. **Right:** `https://socialautopost.online/api/facebook/oauth/callback` (your own domain + `/api/facebook/oauth/callback`). **`FACEBOOK_APP_ID`** must match **App ID** on Meta → Basic exactly (copy-paste; watch for typos).

Local dev: add **`localhost`** to App domains and **`http://127.0.0.1:5000/api/facebook/oauth/callback`** to Valid OAuth Redirect URIs; set the same value in `.env` for `FACEBOOK_REDIRECT_URI`.

### Railway copy-paste (`socialautopost.online`)

From the repo root, run **`python scripts/print_railway_facebook_vars.py`** (optional: pass your domain). It prints **`FACEBOOK_REDIRECT_URI`** and **`PUBLIC_APP_URL`** with **https** — paste those into Railway, then add the same **redirect URI** and **App domains** in Meta (see above). **Do not** use `http://` for your live domain.

## Smoke checks (after deploy)

- `GET https://<your-host>/api/health` → JSON with `"status":"ok"`, `"posting_headless": true` on cloud; `"facebook_oauth_configured": true` and `"facebook_redirect_uri_valid": true` only when **`FACEBOOK_REDIRECT_URI`** is your site’s callback (not a `facebook.com` URL).
- `GET https://<your-host>/api/dashboard` → includes `"posting_headless"`, `"facebook_oauth_configured"`, and `"facebook_redirect_uri_valid"`.

Local (from clone):

```bash
python scripts/verify_api_posting_flags.py
python scripts/verify_playwright_headless.py
```

## Optional

- **`SECRET_KEY`** — Flask cookie signing only; not an API key.
- See **`.env.example`** and **`README.md`** for full context.
