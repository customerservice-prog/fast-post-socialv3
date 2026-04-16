# FastPost — saved deploy reference

Pinned configuration in this repo (commit and redeploy from **`main`**):

| File | Role |
|------|------|
| **`railway.json`** | Railway / Railpack: `buildCommand` → `sh build.sh`, `startCommand` → `sh start.sh`, health → **`GET /api/health`** |
| **`build.sh`** | `pip install -r requirements.txt` + `playwright install --with-deps chromium` (uses **`PLAYWRIGHT_BROWSERS_PATH`** when set) |
| **`start.sh`** | **`/app/.venv/bin/python -m gunicorn`** (falls back to `python`) so the worker always uses the Railpack venv. Playwright browsers are installed in **`build.sh`** only — runtime install is opt-in via **`PLAYWRIGHT_RUNTIME_INSTALL=1`**. |
| **`Procfile`** | `web: sh start.sh` (fallback for platforms that read Procfile) |
| **`render.yaml`** | Optional Render Blueprint only (not used by Railway) |
| **`requirements.txt`** | Python deps at repo root (Railpack install layer) |

## Railway (recommended)

1. Connect this GitHub repo; Railpack picks up **`railway.json`**.
2. **Build vs runtime (Stripe and other secrets):** This app does **not** read `STRIPE_*` (or any billing keys) in `build.sh` / Dockerfile — they are **runtime-only**. If the build fails with **`secret STRIPE_PUBLISHABLE_KEY: not found`** (or similar), Railway’s BuildKit is trying to inject a variable that is not available at build time. In **Railway → Service → Variables**, open each Stripe variable and turn **off** “available at build time” / **build-time** exposure (names vary by UI). Only enable build-time vars if you truly reference them in the install/build command; this repo does not. Redeploy after fixing. **`requirements.txt` already includes `stripe>=11`** — no publishable key is required to `pip install`.
3. **Stripe webhooks:** Configure the endpoint as **`https://<your-domain>/api/stripe/webhook`** (HTTPS). The app also accepts **misconfigured** `POST /` requests that include a **`Stripe-Signature`** header and logs a warning — fix the dashboard URL when convenient.
4. **Variables** (service → Variables):

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
| `PUBLIC_APP_URL` | **Recommended:** `https://YOUR_DOMAIN` only (no path). The app builds the OAuth callback as `PUBLIC_APP_URL` + `/api/facebook/oauth/callback`. |
| `FACEBOOK_REDIRECT_URI` | Optional if `PUBLIC_APP_URL` is set; otherwise required. Must be `https://YOUR_DOMAIN/api/facebook/oauth/callback` and match **Valid OAuth Redirect URIs** in Meta exactly. Never a `facebook.com` URL. |
| `SECRET_KEY` | **Required for production:** long random string for Flask sessions (login). |
| `STRIPE_SECRET_KEY` | Stripe Dashboard — for Checkout, Billing Portal, webhooks. |
| `STRIPE_PUBLISHABLE_KEY` | Stripe publishable key (pricing page / future client use). |
| `STRIPE_WEBHOOK_SECRET` | Webhook signing secret for `POST /api/stripe/webhook`. |
| `STRIPE_STARTER_PRICE_ID` | Price ID for Starter plan ($19/mo product). |
| `STRIPE_GROWTH_PRICE_ID` | Price ID for Growth plan. |
| `STRIPE_AGENCY_PRICE_ID` | Price ID for Agency plan. |
| `ADMIN_EMAIL` | Lowercase email of the owner account; enables **`/admin`**. |
| `MAIL_SERVER` | SMTP host for **password reset** emails (optional; see below). |
| `MAIL_PORT` | SMTP port (default **587** with TLS). Use **465** with `MAIL_USE_SSL=1`. |
| `MAIL_USE_TLS` | `1` for STARTTLS (typical on 587). |
| `MAIL_USE_SSL` | `1` for SMTP_SSL (typical on 465). |
| `MAIL_USERNAME` | SMTP auth user (SendGrid: **`apikey`**). |
| `MAIL_PASSWORD` | SMTP password (SendGrid: API key). |
| `MAIL_DEFAULT_SENDER` | From header, e.g. `FastPost <no-reply@yourdomain.com>`. |

5. **Volume:** mount at **`/data`** and set **`DATABASE_PATH=/data/fastpost.db`**. Without this, SQLite lives on ephemeral disk and **accounts disappear on every redeploy** (the app will log the DB path at startup).
6. **Posting:** runs **headless** on the server — no browser window on your PC (see Settings in the app).
7. **Facebook (recommended):** add a Meta app with **Facebook Login**; set the `FACEBOOK_*` variables and the redirect URI above. In the app: **Accounts → Connect Facebook** once per account, then **Post now** uses the **Graph API** (no JSON files, no Chromium for Facebook). Until the app is **Live**, add your Facebook user under the app’s **Roles** so you can authorize.
8. **Fallback:** **Accounts → Session JSON** (Playwright storage) still works for Instagram-only or if you skip the Meta app. Keep **`PROFILES_DIR` on `/data`** if you use it.
9. **App URL:** The marketing site is **`/`**; the dashboard is **`/dashboard`** (requires login). Set **`PUBLIC_APP_URL`** to `https://YOUR_DOMAIN` so Facebook OAuth, Stripe success/cancel URLs, and password-reset links point at the correct host.
10. **Stripe:** In Stripe Dashboard, create products/prices for Starter / Growth / Agency; paste **Price IDs** into Railway. Add webhook endpoint **`https://YOUR_DOMAIN/api/stripe/webhook`** and select events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`. Paste the **signing secret** into `STRIPE_WEBHOOK_SECRET`.
11. **Password reset email:** If **`MAIL_SERVER`** is unset, reset links are **logged only** (see service logs). To send mail, configure SMTP — e.g. **SendGrid:** `MAIL_SERVER=smtp.sendgrid.net`, `MAIL_PORT=587`, `MAIL_USERNAME=apikey`, `MAIL_PASSWORD=<SendGrid API key>`, `MAIL_DEFAULT_SENDER=FastPost <no-reply@yourdomain.com>`, `MAIL_USE_TLS=1`. Verify the sender domain in SendGrid (or your provider).

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
- **`GET /api/dashboard`** requires a **logged-in session** (cookie). Smoke it in a browser: register at **`/register`**, open **`/dashboard`**, or use `curl` with a cookie jar after `POST /api/auth/login`.
- **`GET /`** → public landing; **`GET /pricing`** → pricing page.

Local (from clone):

```bash
python scripts/verify_api_posting_flags.py
python scripts/verify_playwright_headless.py
```

## Optional

- **`SECRET_KEY`** — Flask cookie signing only; not an API key.
- See **`.env.example`** and **`README.md`** for full context.
