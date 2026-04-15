"""
FastPost Social v3 - Flask Backend Server
Main API server for the AI Social Media Marketing Bot
"""

import os
import json
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlparse

from flask import Flask, request, jsonify, send_from_directory, abort, redirect
from itsdangerous import BadSignature, SignatureExpired
from flask_cors import CORS
from dotenv import load_dotenv
from database import Database
from crawler import BusinessCrawler
from ai_generator import AIContentGenerator
from scheduler import PostScheduler
import facebook_graph
from stealth_poster import (
    StealthPoster,
    PROFILES_DIR,
    UPLOADED_STORAGE_NAME,
    profile_has_headless_session_data,
)

load_dotenv()

logger = logging.getLogger(__name__)

app = Flask(__name__)
# No third-party API keys required; optional SECRET_KEY only for Flask session signing.
app.secret_key = os.getenv("SECRET_KEY", "fastpost-secret-key-change-in-production")
CORS(app, supports_credentials=True)

# Initialize core components
db = Database()
logger.info("SQLite path (set DATABASE_PATH for a persistent volume on Railway): %s", db.db_path)
logger.info("PROFILES_DIR (set to /data/browser_profiles + volume for headless sessions): %s", PROFILES_DIR.resolve())
crawler = BusinessCrawler()
ai_gen = AIContentGenerator()
scheduler = PostScheduler(db=db, ai_gen=ai_gen)
poster = StealthPoster(db=db)

# One worker: Playwright/Chromium is heavy; serializes overlapping "Post now" clicks.
_post_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fastpost_post")

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))


def _public_app_base() -> str:
    """Where the SPA lives — OAuth redirect target. Override with PUBLIC_APP_URL."""
    base = (os.getenv("PUBLIC_APP_URL") or "").strip().rstrip("/")
    if base:
        return base
    redir = (os.getenv("FACEBOOK_REDIRECT_URI") or "").strip()
    if redir:
        p = urlparse(redir)
        return f"{p.scheme}://{p.netloc}".rstrip("/")
    return "http://127.0.0.1:5000"


def _post_via_facebook_graph(account: dict) -> bool:
    plat = (account.get("platform") or "").lower()
    if plat not in ("facebook", "fb", "both"):
        return False
    return bool(account.get("fb_page_id") and account.get("fb_page_access_token"))


def _account_for_api(raw: dict) -> dict:
    """API-safe account view (no huge crawl blob)."""
    keys = (
        "id",
        "platform",
        "page_url",
        "business_url",
        "business_name",
        "created_at",
        "updated_at",
    )
    out = {k: raw.get(k) for k in keys}
    crawl = None
    cd = raw.get("crawl_data")
    if cd:
        try:
            crawl = json.loads(cd) if isinstance(cd, str) else cd
        except (json.JSONDecodeError, TypeError):
            crawl = None
    pages = crawl.get("pages_count") if crawl else None
    out["crawl_pages"] = pages
    out["crawl_ready"] = bool(pages and pages > 0)
    summary = (crawl or {}).get("summary") or ""
    out["crawl_summary_preview"] = (summary[:200] + "…") if len(summary) > 200 else summary
    if out["crawl_ready"]:
        out["status_label"] = "Site indexed"
        out["next_step_hint"] = "Open Daily Queue and tap Generate to build today's drafts."
    else:
        out["status_label"] = "Crawl needs attention"
        out["next_step_hint"] = "Try Re-crawl or confirm your business URL is reachable."
    aid = raw.get("id")
    try:
        iid = int(aid) if aid is not None else None
    except (TypeError, ValueError):
        iid = None
    out["has_playwright_session"] = bool(
        iid is not None
        and profile_has_headless_session_data(PROFILES_DIR / f"profile_{iid}")
    )
    out["fb_graph_connected"] = bool(raw.get("fb_page_access_token"))
    return out


def _playwright_storage_file(account_id: int) -> Path:
    return (PROFILES_DIR / f"profile_{account_id}").resolve() / UPLOADED_STORAGE_NAME


@app.route("/")
def dashboard():
    """Serve the dashboard UI (same origin as API for simple local use)."""
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:filename>")
def frontend_static(filename):
    """CSS/JS/assets for the dashboard."""
    if filename.startswith("api"):
        abort(404)
    path = os.path.join(FRONTEND_DIR, filename)
    if not os.path.isfile(path):
        abort(404)
    return send_from_directory(FRONTEND_DIR, filename)


# ─── ACCOUNT ROUTES ──────────────────────────────────────────────────────────


@app.route("/api/accounts", methods=["GET"])
def get_accounts():
    """Return all linked social media accounts"""
    accounts = [_account_for_api(dict(a)) for a in db.get_all_accounts()]
    return jsonify(
        {
            "accounts": accounts,
            "posting_headless": bool(poster.headless),
            "facebook_oauth_configured": facebook_graph.facebook_oauth_configured(),
        }
    )


@app.route("/api/dashboard", methods=["GET"])
def get_dashboard():
    """Single payload for the Daily Queue UX: drafts, history, stats, account chips."""
    today = date.today().strftime("%Y-%m-%d")
    return jsonify(
        {
            "pending_today": db.get_todays_queue(),
            "pending_other_days": db.get_pending_other_days(today, limit=20),
            "published_today_count": db.count_published_today(),
            "recent_published": db.get_recent_published_all(15),
            "accounts": [_account_for_api(dict(a)) for a in db.get_all_accounts()],
            # False only on a desktop with FB_HEADED=1; cloud is always True (no window on your PC).
            "posting_headless": bool(poster.headless),
            "facebook_oauth_configured": facebook_graph.facebook_oauth_configured(),
        }
    )


def _empty_crawl_payload(business_url: str, err: str) -> dict:
    """Minimal crawl shape if the live crawl throws (account still saved)."""
    u = (business_url or "").strip()
    if u and not u.startswith("http"):
        u = "https://" + u
    return {
        "base_url": u or "",
        "business_name": "",
        "pages_count": 0,
        "services": [],
        "prices": [],
        "key_headings": [],
        "image_descriptions": [],
        "text_samples": [],
        "summary": "",
        "crawl_error": err[:500],
    }


@app.route("/api/accounts", methods=["POST"])
def add_account():
    """Link a new social media account"""
    data = request.get_json(silent=True) or {}
    required = ["platform", "page_url", "business_url", "business_name"]
    if not isinstance(data, dict) or not all(k in data for k in required):
        return jsonify({"error": "Missing required fields"}), 400

    def _s(v) -> str:
        if v is None:
            return ""
        return str(v).strip()

    fields = {k: _s(data.get(k)) for k in required}
    if not all(fields[k] for k in required):
        return jsonify({"error": "All fields must be non-empty"}), 400

    account_id = db.add_account(
        platform=fields["platform"],
        page_url=fields["page_url"],
        business_url=fields["business_url"],
        business_name=fields["business_name"],
        session_data=None,
    )
    crawl_ok = True
    crawl_err = ""
    try:
        crawl_result = crawler.crawl(fields["business_url"])
    except Exception as e:
        logger.exception("Initial crawl failed for account_id=%s", account_id)
        crawl_ok = False
        crawl_err = str(e) or "crawl failed"
        crawl_result = _empty_crawl_payload(fields["business_url"], crawl_err)

    db.update_crawl_data(account_id, crawl_result)

    if crawl_ok:
        return jsonify({"id": account_id, "message": "Account linked and site crawled", "crawl_ok": True}), 201
    return (
        jsonify(
            {
                "id": account_id,
                "message": "Account saved; initial crawl failed — use Re-crawl or check the business URL.",
                "crawl_ok": False,
                "crawl_error": crawl_err,
            }
        ),
        201,
    )


@app.route("/api/accounts/<int:account_id>", methods=["DELETE"])
def delete_account(account_id):
    """Remove a linked account"""
    db.delete_account(account_id)
    return jsonify({"message": "Account removed"})


@app.route("/api/accounts/<int:account_id>/playwright-storage", methods=["GET"])
def playwright_storage_status(account_id):
    """Whether headless posting has session data (uploaded JSON and/or Chromium profile on disk)."""
    if not db.get_account(account_id):
        return jsonify({"error": "Account not found"}), 404
    pdir = (PROFILES_DIR / f"profile_{account_id}").resolve()
    path = _playwright_storage_file(account_id)
    return jsonify(
        {
            "has_session": profile_has_headless_session_data(pdir),
            "has_uploaded_json": path.is_file(),
        }
    )


@app.route("/api/accounts/<int:account_id>/playwright-storage", methods=["POST"])
def playwright_storage_save(account_id):
    """Save Playwright storage_state JSON (cookies + origins) for headless cloud posting."""
    if not db.get_account(account_id):
        return jsonify({"error": "Account not found"}), 404
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Body must be a JSON object (Playwright storage state)"}), 400
    cookies = data.get("cookies")
    if not isinstance(cookies, list) or len(cookies) == 0:
        return jsonify({"error": "Expected a non-empty 'cookies' array (Playwright storage format)"}), 400
    path = _playwright_storage_file(account_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    logger.info("Saved Playwright storage for account_id=%s at %s", account_id, path)
    return jsonify({"message": "Session saved", "has_session": True})


@app.route("/api/accounts/<int:account_id>/playwright-storage", methods=["DELETE"])
def playwright_storage_clear(account_id):
    """Remove uploaded Playwright storage for this account."""
    if not db.get_account(account_id):
        return jsonify({"error": "Account not found"}), 404
    path = _playwright_storage_file(account_id)
    if path.is_file():
        path.unlink()
    return jsonify({"message": "Session cleared", "has_session": False})


@app.route("/api/accounts/<int:account_id>/facebook/disconnect", methods=["POST"])
def facebook_graph_disconnect(account_id):
    """Clear Facebook Graph API tokens (use Connect again to re-authorize)."""
    if not db.get_account(account_id):
        return jsonify({"error": "Account not found"}), 404
    db.clear_facebook_graph_token(account_id)
    return jsonify({"message": "Facebook disconnected", "fb_graph_connected": False})


@app.route("/api/facebook/oauth/start", methods=["GET"])
def facebook_oauth_start():
    """Redirect to Meta login; after approval, user returns to /api/facebook/oauth/callback."""
    if not facebook_graph.facebook_oauth_configured():
        return jsonify(
            {
                "error": (
                    "Facebook Login is not configured. Set FACEBOOK_APP_ID, FACEBOOK_APP_SECRET, "
                    "and FACEBOOK_REDIRECT_URI in Railway (see DEPLOY.md)."
                )
            }
        ), 503
    account_id = request.args.get("account_id", type=int)
    if not account_id:
        return jsonify({"error": "account_id query parameter is required"}), 400
    if not db.get_account(account_id):
        return jsonify({"error": "Account not found"}), 404
    url = facebook_graph.oauth_authorize_url(account_id, app.secret_key)
    return redirect(url)


@app.route("/api/facebook/oauth/callback", methods=["GET"])
def facebook_oauth_callback():
    """Meta redirects here with ?code=&state= — exchange token and store Page access token."""
    err = request.args.get("error_description") or request.args.get("error")
    if err:
        return redirect(f"{_public_app_base()}/?fb_error={quote(err[:500])}")
    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state:
        return redirect(f"{_public_app_base()}/?fb_error={quote('Missing authorization code')}")
    try:
        aid = facebook_graph.parse_oauth_state(state, app.secret_key)
    except SignatureExpired:
        return redirect(
            f"{_public_app_base()}/?fb_error={quote('Session expired — open Accounts and click Connect Facebook again')}"
        )
    except BadSignature:
        return redirect(f"{_public_app_base()}/?fb_error={quote('Invalid OAuth state')}")
    ok, msg = facebook_graph.complete_oauth_and_store(db, aid, code)
    if ok:
        return redirect(f"{_public_app_base()}/?fb_connected=1")
    return redirect(f"{_public_app_base()}/?fb_error={quote(msg or 'Facebook connection failed')}")


# ─── CRAWL ROUTES ────────────────────────────────────────────────────────────


@app.route("/api/crawl/<int:account_id>", methods=["POST"])
def recrawl(account_id):
    """Re-crawl the business website to refresh content"""
    account = db.get_account(account_id)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    result = crawler.crawl(account["business_url"])
    db.update_crawl_data(account_id, result)
    return jsonify({"message": "Crawl complete", "pages_found": result.get("pages_count", 0)})


# ─── POST QUEUE ROUTES ───────────────────────────────────────────────────────


@app.route("/api/queue", methods=["GET"])
def get_queue():
    """Return today's post queue across all accounts"""
    posts = db.get_todays_queue()
    return jsonify({"posts": posts})


@app.route("/api/queue/generate", methods=["POST"])
def generate_posts():
    """Generate AI posts for all accounts for today"""
    data = request.json or {}
    account_id = data.get("account_id")  # Optional: generate for specific account

    if account_id:
        accounts = [db.get_account(account_id)]
    else:
        accounts = db.get_all_accounts()

    generated = []
    for account in accounts:
        if not account:
            continue
        crawl_data = db.get_crawl_data(account["id"])
        posts = ai_gen.generate_daily_posts(
            business_name=account["business_name"],
            business_url=account["business_url"],
            platform=account["platform"],
            crawl_data=crawl_data,
        )
        for post in posts:
            post_id = db.add_post(
                account_id=account["id"],
                caption=post["caption"],
                post_type=post["type"],
                scheduled_time=post["scheduled_time"],
                image_prompt=post.get("image_prompt", ""),
            )
            generated.append(
                {"id": post_id, "type": post["type"], "account": account["business_name"]}
            )

    return jsonify({"generated": generated, "count": len(generated)})


@app.route("/api/queue/<int:post_id>", methods=["GET"])
def get_post(post_id):
    """Get a specific post from the queue"""
    post = db.get_post(post_id)
    if not post:
        return jsonify({"error": "Post not found — refresh the queue."}), 404
    return jsonify(post)


@app.route("/api/queue/<int:post_id>", methods=["PUT"])
def update_post(post_id):
    """Edit a post caption before publishing"""
    data = request.json
    db.update_post_caption(post_id, data.get("caption", ""))
    return jsonify({"message": "Post updated"})


@app.route("/api/queue/<int:post_id>", methods=["DELETE"])
def delete_post(post_id):
    """Delete a post from the queue"""
    db.delete_post(post_id)
    return jsonify({"message": "Post deleted"})


# ─── POSTING ROUTES ──────────────────────────────────────────────────────────


@app.route("/api/post/<int:post_id>", methods=["POST"])
def post_now(post_id):
    """
    Human-in-the-Loop: User triggered this.
    Launches stealth browser to post to social media.
    """
    post = db.get_post(post_id)
    if not post:
        return jsonify(
            {
                "error": (
                    "Post not found — it may have been deleted, or the server has a different "
                    "database than when this page loaded. Refresh the queue and try again. "
                    "(If you scaled to multiple instances without a shared disk/DB, use one instance "
                    "or an external database.)"
                )
            }
        ), 404

    account = db.get_account(post["account_id"])
    if not account:
        return jsonify({"error": "Account not found"}), 404

    # Facebook Graph API: no Playwright — user clicked "Connect Facebook" once (OAuth).
    if _post_via_facebook_graph(account):
        ok, err_msg = facebook_graph.post_page_feed(
            str(account["fb_page_id"]),
            account["fb_page_access_token"],
            post["caption"],
        )
        if ok:
            db.mark_post_published(post_id)
            return jsonify(
                {
                    "message": "Posted successfully",
                    "post_id": post_id,
                    "posting_headless": bool(poster.headless),
                    "method": "facebook_graph",
                }
            )
        return jsonify({"error": err_msg or "Facebook API post failed"}), 500

    # Stay below ~15m platform HTTP limits (e.g. Railway) so we return JSON 504 instead of a dropped socket.
    # Raise POST_TIMEOUT_SECONDS locally if headed login needs longer.
    timeout_s = int(os.getenv("POST_TIMEOUT_SECONDS", "840"))
    try:
        fut = _post_executor.submit(
            poster.post,
            account["platform"],
            account["page_url"],
            post["caption"],
            account["id"],
        )
        result = fut.result(timeout=timeout_s)
    except FutureTimeout:
        return jsonify(
            {
                "error": (
                    f"Posting ran longer than {timeout_s}s and was stopped. "
                    "On the server, refresh your Playwright session under Accounts, confirm the Page URL, "
                    "or raise POST_TIMEOUT_SECONDS if you use a headed browser locally."
                )
            }
        ), 504
    except Exception as e:
        logger.exception("post_now crashed")
        return jsonify({"error": str(e) or "Posting failed unexpectedly"}), 500

    if result["success"]:
        db.mark_post_published(post_id)
        return jsonify(
            {
                "message": "Posted successfully",
                "post_id": post_id,
                "posting_headless": bool(poster.headless),
            }
        )
    else:
        return jsonify({"error": result.get("error", "Unknown error")}), 500


# ─── ANALYTICS ROUTES ────────────────────────────────────────────────────────


@app.route("/api/analytics", methods=["GET"])
def get_analytics():
    """Return post performance analytics"""
    stats = db.get_analytics()
    stats["recent_posts"] = db.get_recent_published_all(40)
    return jsonify(stats)


@app.route("/api/analytics/<int:account_id>", methods=["GET"])
def get_account_analytics(account_id):
    """Return analytics for a specific account"""
    stats = db.get_account_analytics(account_id)
    return jsonify(stats)


# ─── SCHEDULER ROUTES ────────────────────────────────────────────────────────


@app.route("/api/scheduler/start", methods=["POST"])
def start_scheduler():
    """Start the automatic post generation scheduler"""
    scheduler.start()
    return jsonify({"message": "Scheduler started"})


@app.route("/api/scheduler/stop", methods=["POST"])
def stop_scheduler():
    """Stop the scheduler"""
    scheduler.stop()
    return jsonify({"message": "Scheduler stopped"})


@app.route("/api/scheduler/status", methods=["GET"])
def scheduler_status():
    """Get scheduler status"""
    return jsonify({"running": scheduler.is_running(), "next_run": scheduler.next_run()})


# ─── HEALTH CHECK ────────────────────────────────────────────────────────────


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify(
        {
            "status": "ok",
            "version": "3.0.0",
            "posting_headless": bool(poster.headless),
            "facebook_oauth_configured": facebook_graph.facebook_oauth_configured(),
        }
    )


# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db.init_db()
    scheduler.start()
    # threaded=True: Playwright can run for minutes; without threads the dev server blocks all other requests.
    # use_reloader: set FLASK_RELOADER=1 to enable watchdog (reload can interrupt a long post mid-flight).
    use_reloader = os.environ.get("FLASK_RELOADER", "").lower() in ("1", "true", "yes")
    _debug = os.environ.get("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")
    app.run(
        debug=_debug,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        threaded=True,
        use_reloader=use_reloader,
    )
