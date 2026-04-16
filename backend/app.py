"""
FastPost Social v3 - Flask Backend Server
Main API server for the AI Social Media Marketing Bot
"""

import os
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

import stripe

SignatureVerificationError = stripe.SignatureVerificationError

from flask import (
    Flask,
    request,
    jsonify,
    send_from_directory,
    abort,
    redirect,
    render_template,
    url_for,
)
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from itsdangerous import BadSignature, SignatureExpired
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.security import check_password_hash

from auth_models import User
from database import Database
from email_service import mail_is_configured, send_password_reset_email
from crawler import BusinessCrawler
from ai_generator import AIContentGenerator
from scheduler import PostScheduler
import facebook_graph
from publish_service import publish_post_with_deps
from subscription_limits import (
    account_week_approved_for_current_week,
    can_add_business,
    effective_posts_per_day_for_account,
    max_posts_per_day_for_plan,
    effective_plan_code,
)
from stealth_poster import (
    StealthPoster,
    PROFILES_DIR,
    UPLOADED_STORAGE_NAME,
    profile_has_headless_session_data,
)

load_dotenv()

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(_BACKEND_DIR / "templates"))
app.secret_key = os.getenv("SECRET_KEY", "fastpost-secret-key-change-in-production")
CORS(app, supports_credentials=True)

db = Database()
logger.info("SQLite path (set DATABASE_PATH for a persistent volume on Railway): %s", db.db_path)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login_page"
login_manager.session_protection = "strong"


@login_manager.user_loader
def load_user(user_id):
    row = db.get_user_by_id(int(user_id))
    if not row:
        return None
    return User(row)


@login_manager.unauthorized_handler
def _unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Login required"}), 401
    next_url = request.path if request.path else ""
    if request.query_string:
        next_url += "?" + request.query_string.decode()
    return redirect(url_for("login_page", next=next_url))


_stripe_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
if _stripe_key:
    stripe.api_key = _stripe_key


def _log_startup_configuration_warnings() -> None:
    """Surface missing production config in logs (no silent feature disable)."""
    railway = bool(
        (os.getenv("RAILWAY_ENVIRONMENT") or "").strip()
        or (os.getenv("RAILWAY_PROJECT_ID") or "").strip()
    )
    sk = (os.getenv("SECRET_KEY") or "").strip()
    if not sk or sk == "fastpost-secret-key-change-in-production":
        logger.warning(
            "SECRET_KEY is missing or still the default — set a long random string in production for Flask sessions."
        )
    if not _stripe_key:
        logger.warning(
            "STRIPE_SECRET_KEY is unset — Stripe Checkout, Billing Portal, and subscription API calls are disabled."
        )
    if not (os.getenv("STRIPE_PUBLISHABLE_KEY") or "").strip():
        logger.warning(
            "STRIPE_PUBLISHABLE_KEY is unset — client-side Stripe (e.g. Elements) on marketing pages will not initialize."
        )
    if not (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip():
        logger.warning(
            "STRIPE_WEBHOOK_SECRET is unset — webhook endpoint will respond 503 until set (use https://<host>/api/stripe/webhook in Stripe)."
        )
    for label, railway_key, legacy_key in (
        ("Starter", "STRIPE_PRICE_ID_STARTER", "STRIPE_STARTER_PRICE_ID"),
        ("Growth",  "STRIPE_PRICE_ID_GROWTH",  "STRIPE_GROWTH_PRICE_ID"),
        ("Agency",  "STRIPE_PRICE_ID_AGENCY",  "STRIPE_AGENCY_PRICE_ID"),
    ):
        val = (os.getenv(railway_key) or os.getenv(legacy_key) or "").strip()
        if not val:
            logger.warning(
                "%s price ID is unset — set %s (or %s) in Railway env vars for Stripe Checkout/plan sync.",
                label,
                railway_key,
                legacy_key,
            )
    if not mail_is_configured():
        logger.warning(
            "MAIL_SERVER is unset — password reset emails are not sent; links are logged only."
        )
    if (os.getenv("OPENAI_API_KEY") or "").strip():
        logger.warning(
            "OPENAI_API_KEY is set but unused — captions are generated locally from crawl data (no OpenAI calls)."
        )
    elif railway:
        logger.info(
            "OPENAI_API_KEY not required: content uses built-in templates (see NO_API_KEYS.md)."
        )


# Initialize core components
logger.info("PROFILES_DIR (set to /data/browser_profiles + volume for headless sessions): %s", PROFILES_DIR.resolve())
facebook_graph.log_facebook_oauth_env_warnings()
_log_startup_configuration_warnings()
crawler = BusinessCrawler()
ai_gen = AIContentGenerator()

# One worker: Playwright/Chromium is heavy; serializes overlapping "Post now" clicks.
_post_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fastpost_post")

poster = StealthPoster(db=db)
scheduler = PostScheduler(db=db, ai_gen=ai_gen, poster=poster, post_executor=_post_executor)

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))


def _admin_email() -> str:
    return (os.getenv("ADMIN_EMAIL") or "").strip().lower()


def _is_admin_user() -> bool:
    if not current_user.is_authenticated:
        return False
    ae = _admin_email()
    return bool(ae) and str(current_user.email).lower() == ae


def admin_required(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not _is_admin_user():
            abort(403)
        return f(*args, **kwargs)

    return decorated


def _stripe_price_for_plan(plan: str) -> Optional[str]:
    plan = (plan or "").lower().strip()
    # Support both naming conventions:
    #   STRIPE_PRICE_ID_STARTER  — Railway-standard (preferred)
    #   STRIPE_STARTER_PRICE_ID  — legacy fallback
    def _pid(railway_key: str, legacy_key: str) -> str:
        v = (os.getenv(railway_key) or os.getenv(legacy_key) or "").strip()
        return v
    m = {
        "starter": _pid("STRIPE_PRICE_ID_STARTER", "STRIPE_STARTER_PRICE_ID"),
        "growth":  _pid("STRIPE_PRICE_ID_GROWTH",  "STRIPE_GROWTH_PRICE_ID"),
        "agency":  _pid("STRIPE_PRICE_ID_AGENCY",  "STRIPE_AGENCY_PRICE_ID"),
    }
    pid = (m.get(plan) or "").strip()
    return pid or None


def _plan_for_stripe_price(price_id: str) -> Optional[str]:
    price_id = (price_id or "").strip()
    if not price_id:
        return None
    # Support both naming conventions: STRIPE_PRICE_ID_* (Railway) and STRIPE_*_PRICE_ID (legacy)
    def _get_price(railway_key: str, legacy_key: str) -> str:
        return (os.getenv(railway_key) or os.getenv(legacy_key) or "").strip()
    if price_id == _get_price("STRIPE_PRICE_ID_STARTER", "STRIPE_STARTER_PRICE_ID"):
        return "starter"
    if price_id == _get_price("STRIPE_PRICE_ID_GROWTH", "STRIPE_GROWTH_PRICE_ID"):
        return "growth"
    if price_id == _get_price("STRIPE_PRICE_ID_AGENCY", "STRIPE_AGENCY_PRICE_ID"):
        return "agency"
    return None


def _public_app_base() -> str:
    """Where the SPA lives — OAuth redirect target. Override with PUBLIC_APP_URL."""
    base = (os.getenv("PUBLIC_APP_URL") or "").strip().rstrip("/")
    if base:
        return base
    redir = (os.getenv("FACEBOOK_REDIRECT_URI") or "").strip()
    if redir:
        p = urlparse(redir)
        host = (p.hostname or "").lower()
        if host and "facebook.com" not in host:
            return f"{p.scheme}://{p.netloc}".rstrip("/")
    rdom = (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    if rdom and "facebook.com" not in rdom.lower():
        rdom = rdom.split("/")[0].split(":")[0]
        return f"https://{rdom}"
    return "http://127.0.0.1:5000"


def _forwarded_scheme_host():
    """Scheme and host as seen by the client (Railway sets X-Forwarded-*)."""
    proto = request.headers.get("X-Forwarded-Proto") or request.scheme or "https"
    if "," in proto:
        proto = proto.split(",", 1)[0].strip()
    host = request.headers.get("X-Forwarded-Host") or request.host or ""
    if "," in host:
        host = host.split(",", 1)[0].strip()
    return proto, host


def _facebook_redirect_for_request() -> Optional[str]:
    sch, host = _forwarded_scheme_host()
    return facebook_graph.facebook_effective_redirect_uri(
        forwarded_scheme=sch,
        forwarded_host=host,
    )


def _facebook_api_extras():
    """Dashboard/health fields: redirect URL as this browser request would use for OAuth."""
    eff = _facebook_redirect_for_request()
    return {
        "facebook_redirect_uri_valid": eff is not None,
        "facebook_oauth_redirect_uri": eff or "",
    }


def _suggested_facebook_callback_url() -> str:
    """Exact redirect URI to show in errors — set this in Railway and Meta (same string)."""
    eff = _facebook_redirect_for_request()
    if eff:
        return eff
    eff = facebook_graph.facebook_effective_redirect_uri()
    if eff:
        return eff
    suffix = "/api/facebook/oauth/callback"
    pub = (os.getenv("PUBLIC_APP_URL") or "").strip().rstrip("/")
    if pub:
        return f"{pub}{suffix}"
    for key in ("RAILWAY_PUBLIC_DOMAIN",):
        d = (os.getenv(key) or "").strip()
        if not d or "facebook.com" in d.lower():
            continue
        d = d.split("/")[0].split(":")[0]
        if d:
            return f"https://{d}{suffix}"
    uri = (os.getenv("FACEBOOK_REDIRECT_URI") or "").strip()
    if uri:
        p = urlparse(uri)
        host = (p.hostname or "").lower()
        if host and "facebook.com" not in host:
            base = f"{p.scheme or 'https'}://{p.netloc}".rstrip("/")
            return f"{base}{suffix}"
    return f"https://<your-domain>{suffix}"




def _account_for_api(raw: dict, posts_sent: int = 0) -> dict:
    """API-safe account view (no huge crawl blob)."""
    keys = (
        "id",
        "platform",
        "page_url",
        "business_url",
        "business_name",
        "created_at",
        "updated_at",
        "posts_per_day",
        "weekly_approved_iso_week",
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
    out["posts_sent_count"] = int(posts_sent)
    try:
        ppd = int(raw.get("posts_per_day") or 3)
    except (TypeError, ValueError):
        ppd = 3
    out["posts_per_day"] = max(1, min(3, ppd))
    out["week_approval_active"] = account_week_approved_for_current_week(raw)
    return out


def _playwright_storage_file(account_id: int) -> Path:
    return (PROFILES_DIR / f"profile_{account_id}").resolve() / UPLOADED_STORAGE_NAME


@app.route("/", methods=["GET", "POST"])
def landing_page():
    """Public marketing landing. POST / accepts Stripe webhooks if dashboard URL is mis-set to site root."""
    if request.method == "POST":
        if not request.headers.get("Stripe-Signature"):
            abort(405)
        logger.warning(
            "Stripe webhook hit POST / — update Stripe Dashboard endpoint to .../api/stripe/webhook (root still works as fallback)."
        )
        return _stripe_webhook_process()
    pk = (os.getenv("STRIPE_PUBLISHABLE_KEY") or "").strip()
    return render_template("landing.html", stripe_publishable_key=pk)


@app.route("/pricing")
def pricing_page():
    pk = (os.getenv("STRIPE_PUBLISHABLE_KEY") or "").strip()
    return render_template("pricing.html", stripe_publishable_key=pk)


@app.route("/login")
def login_page():
    nxt = request.args.get("next") or "/dashboard"
    if not nxt.startswith("/"):
        nxt = "/dashboard"
    return render_template("login.html", next_url=nxt)


@app.route("/register")
def register_page():
    return render_template("register.html")


@app.route("/forgot-password")
def forgot_password_page():
    return render_template("forgot_password.html")


@app.route("/reset-password")
def reset_password_page():
    return render_template("reset_password.html", token=request.args.get("token") or "")


@app.route("/billing")
@login_required
def billing_page():
    return render_template("billing.html")


@app.route("/admin")
@login_required
@admin_required
def admin_page():
    return render_template("admin.html")


@app.route("/admin/login")
def admin_login_page():
    """Dedicated admin login page — redirects to /admin if already admin."""
    if current_user.is_authenticated and _is_admin_user():
        return redirect("/admin")
    return render_template("admin_login.html", next_url="/admin")


@app.route("/dashboard")
@login_required
def dashboard_app():
    """Authenticated SPA shell."""
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


# ─── AUTH API ────────────────────────────────────────────────────────────────


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    if not current_user.is_authenticated:
        return jsonify({"user": None})
    row = db.get_user_by_id(current_user.id)
    if not row:
        return jsonify({"user": None})
    u = User(row)
    out = u.to_public_dict()
    out["effective_plan"] = effective_plan_code(row)
    out["max_posts_per_day_cap"] = max_posts_per_day_for_plan(effective_plan_code(row))
    return jsonify({"user": out})


@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    name = (data.get("display_name") or "").strip()
    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if db.get_user_by_email(email):
        return jsonify({"error": "An account with this email already exists"}), 409
    uid = db.create_user(email, password, display_name=name, trial_days=7)
    row = db.get_user_by_id(uid)
    login_user(User(row), remember=True)
    return jsonify({"ok": True, "user": User(row).to_public_dict()})


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    row = db.get_user_by_email(email)
    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify({"error": "Invalid email or password"}), 401
    login_user(User(row), remember=bool(data.get("remember")))
    return jsonify({"ok": True, "user": User(row).to_public_dict()})


@app.route("/api/auth/logout", methods=["POST"])
@login_required
def auth_logout():
    logout_user()
    return jsonify({"ok": True})


@app.route("/api/auth/forgot-password", methods=["POST"])
def auth_forgot_password():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    row = db.get_user_by_email(email) if email else None
    if row:
        raw = db.create_password_reset_token(int(row["id"]))
        base = _public_app_base().rstrip("/")
        link = f"{base}/reset-password?token={quote(raw)}"
        if mail_is_configured():
            ok, err = send_password_reset_email(email, link)
            if not ok:
                logger.warning("Password reset email failed for %s: %s — link: %s", email, err, link)
        else:
            logger.info("Password reset link for %s (email not configured): %s", email, link)
    # Same response whether or not the address exists (avoid account enumeration).
    return jsonify(
        {
            "ok": True,
            "message": "If that email is registered, you will receive password reset instructions shortly.",
        }
    )


@app.route("/api/auth/reset-password", methods=["POST"])
def auth_reset_password_submit():
    data = request.get_json(silent=True) or {}
    token = data.get("token") or ""
    password = data.get("password") or ""
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    uid = db.consume_password_reset_token(str(token))
    if not uid:
        return jsonify({"error": "Invalid or expired reset link"}), 400
    db.set_user_password(uid, password)
    row = db.get_user_by_id(uid)
    if row:
        login_user(User(row), remember=True)
    return jsonify({"ok": True})


# ─── STRIPE ───────────────────────────────────────────────────────────────────


@app.route("/api/billing/checkout", methods=["POST"])
@login_required
def billing_checkout():
    if not _stripe_key:
        return jsonify({"error": "Stripe is not configured"}), 503
    data = request.get_json(silent=True) or {}
    plan = (data.get("plan") or "").lower().strip()
    price = _stripe_price_for_plan(plan)
    if not price:
        return jsonify({"error": "Unknown plan or price not configured"}), 400
    base = _public_app_base().rstrip("/")
    row = db.get_user_by_id(current_user.id)
    cust = (row.get("stripe_customer_id") or "").strip() if row else ""
    try:
        kwargs = {
            "mode": "subscription",
            "client_reference_id": str(current_user.id),
            "line_items": [{"price": price, "quantity": 1}],
            "success_url": base + "/billing?session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": base + "/pricing",
            "metadata": {"user_id": str(current_user.id), "plan": plan},
        }
        if cust:
            kwargs["customer"] = cust
        else:
            kwargs["customer_email"] = current_user.email
        sess = stripe.checkout.Session.create(**kwargs)
    except Exception as e:
        logger.exception("Stripe checkout failed")
        return jsonify({"error": str(e) or "Checkout failed"}), 500
    return jsonify({"url": sess.url})


@app.route("/api/billing/portal", methods=["POST"])
@login_required
def billing_portal():
    if not _stripe_key:
        return jsonify({"error": "Stripe is not configured"}), 503
    row = db.get_user_by_id(current_user.id)
    cust = (row.get("stripe_customer_id") or "").strip() if row else ""
    if not cust:
        return jsonify({"error": "No Stripe customer yet — subscribe first"}), 400
    base = _public_app_base().rstrip("/")
    try:
        sess = stripe.billing_portal.Session.create(
            customer=cust,
            return_url=base + "/billing",
        )
    except Exception as e:
        logger.exception("Stripe portal failed")
        return jsonify({"error": str(e) or "Portal failed"}), 500
    return jsonify({"url": sess.url})


def _stripe_webhook_process():
    """Shared Stripe webhook handler (POST body + Stripe-Signature required)."""
    wh_secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    payload = request.get_data(as_text=False)
    sig = request.headers.get("Stripe-Signature") or ""
    if not wh_secret:
        return jsonify({"error": "Webhook not configured"}), 503
    try:
        event = stripe.Webhook.construct_event(payload=payload, sig_header=sig, secret=wh_secret)
    except ValueError:
        return jsonify({"error": "Invalid payload"}), 400
    except SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400

    et = event["type"]
    obj = event["data"]["object"]

    try:
        if et == "checkout.session.completed":
            uid = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("user_id")
            if uid:
                uid = int(uid)
                customer_id = obj.get("customer")
                sub_id = obj.get("subscription")
                if customer_id:
                    db.update_user_subscription_fields(uid, stripe_customer_id=customer_id)
                if sub_id:
                    sub = stripe.Subscription.retrieve(sub_id)
                    price_id = None
                    try:
                        price_id = sub["items"]["data"][0]["price"]["id"]
                    except (KeyError, IndexError, TypeError):
                        pass
                    plan = _plan_for_stripe_price(price_id) if price_id else None
                    st = (sub.get("status") or "").lower()
                    cpe = sub.get("current_period_end")
                    cpe_iso = None
                    if cpe:
                        try:
                            cpe_iso = datetime.fromtimestamp(int(cpe), tz=timezone.utc).isoformat()
                        except (ValueError, TypeError, OSError):
                            cpe_iso = None
                    db.update_user_subscription_fields(
                        uid,
                        stripe_subscription_id=sub_id,
                        stripe_price_id=price_id,
                        plan_code=plan or "starter",
                        subscription_status=st or "active",
                        subscription_current_period_end=cpe_iso,
                    )
        elif et in ("customer.subscription.updated", "customer.subscription.deleted"):
            sub = obj
            sub_id = sub.get("id")
            customer_id = sub.get("customer")
            st = (sub.get("status") or "").lower()
            uid = None
            if customer_id:
                conn = db.get_conn()
                r = conn.execute(
                    "SELECT id FROM users WHERE stripe_customer_id = ?", (customer_id,)
                ).fetchone()
                conn.close()
                if r:
                    uid = int(r["id"])
            if not uid and sub_id:
                conn = db.get_conn()
                r = conn.execute(
                    "SELECT id FROM users WHERE stripe_subscription_id = ?", (sub_id,)
                ).fetchone()
                conn.close()
                if r:
                    uid = int(r["id"])
            if uid:
                price_id = None
                try:
                    items = sub.get("items", {}).get("data") or []
                    if items:
                        price_id = items[0].get("price", {}).get("id")
                except (IndexError, TypeError):
                    pass
                plan = _plan_for_stripe_price(price_id) if price_id else None
                cpe_iso = None
                cpe = sub.get("current_period_end")
                if cpe:
                    try:
                        cpe_iso = datetime.fromtimestamp(int(cpe), tz=timezone.utc).isoformat()
                    except (ValueError, TypeError, OSError):
                        cpe_iso = None
                if et == "customer.subscription.deleted":
                    db.update_user_subscription_fields(
                        uid,
                        stripe_subscription_id=None,
                        subscription_status="canceled",
                        subscription_current_period_end=cpe_iso,
                    )
                else:
                    db.update_user_subscription_fields(
                        uid,
                        stripe_subscription_id=sub_id,
                        stripe_price_id=price_id,
                        plan_code=plan or db.get_user_by_id(uid).get("plan_code"),
                        subscription_status=st or "active",
                        subscription_current_period_end=cpe_iso,
                    )
    except Exception:
        logger.exception("Stripe webhook handler error")
        return jsonify({"received": True}), 200

    return jsonify({"received": True})


@app.route("/api/stripe/webhook", methods=["POST"])
def stripe_webhook():
    return _stripe_webhook_process()


# ─── ADMIN API ────────────────────────────────────────────────────────────────


@app.route("/api/admin/summary", methods=["GET"])
@login_required
@admin_required
def admin_summary():
    users = db.list_all_users()
    return jsonify(
        {
            "users": users,
            "total_users": len(users),
            "total_accounts": db.count_all_accounts_global(),
            "total_published_posts": db.count_all_published_posts_global(),
            "admin_email": _admin_email(),
        }
    )


@app.route("/api/admin/users/<int:user_id>/subscription", methods=["POST"])
@login_required
@admin_required
def admin_override_subscription(user_id):
    data = request.get_json(silent=True) or {}
    plan = (data.get("plan_code") or "starter").strip().lower()
    st = (data.get("subscription_status") or "active").strip().lower()
    ok = db.admin_set_user_plan(user_id, plan, st)
    if not ok:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"ok": True})


# ─── ACCOUNT ROUTES ──────────────────────────────────────────────────────────


@app.route("/api/accounts", methods=["GET"])
@login_required
def get_accounts():
    """Return linked social media accounts for the current user."""
    uid = current_user.id
    hist = db.get_post_history_counts(uid)
    accounts = [
        _account_for_api(dict(a), hist.get(int(a["id"]), 0))
        for a in db.get_accounts_for_user(uid)
    ]
    row = db.get_user_by_id(uid)
    can, reason = can_add_business(row, db.count_accounts_for_user(uid))
    return jsonify(
        {
            "accounts": accounts,
            "plan": effective_plan_code(row) if row else "trial",
            "can_add_business": can,
            "add_business_blocked_reason": reason,
            "max_posts_per_day_cap": max_posts_per_day_for_plan(effective_plan_code(row)) if row else 1,
            "posting_headless": bool(poster.headless),
            "facebook_oauth_configured": facebook_graph.facebook_oauth_configured(),
            **_facebook_api_extras(),
        }
    )


@app.route("/api/dashboard", methods=["GET"])
@login_required
def get_dashboard():
    """Single payload for the Daily Queue UX: drafts, history, stats, account chips."""
    uid = current_user.id
    today = date.today().strftime("%Y-%m-%d")
    hist_counts = db.get_post_history_counts(uid)
    row = db.get_user_by_id(uid)
    can, reason = can_add_business(row, db.count_accounts_for_user(uid))
    return jsonify(
        {
            "pending_today": db.get_todays_queue(uid),
            "pending_other_days": db.get_pending_other_days(today, limit=20, user_id=uid),
            "published_today_count": db.count_published_today(uid),
            "recent_published": db.get_recent_published_all(15, user_id=uid),
            "accounts": [
                _account_for_api(dict(a), hist_counts.get(int(a["id"]), 0))
                for a in db.get_accounts_for_user(uid)
            ],
            "plan": effective_plan_code(row) if row else "trial",
            "can_add_business": can,
            "add_business_blocked_reason": reason,
            "max_posts_per_day_cap": max_posts_per_day_for_plan(effective_plan_code(row)) if row else 1,
            "posting_headless": bool(poster.headless),
            "facebook_oauth_configured": facebook_graph.facebook_oauth_configured(),
            **_facebook_api_extras(),
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
@login_required
def add_account():
    """Link a new social media account"""
    uid = current_user.id
    row = db.get_user_by_id(uid)
    n_accounts = db.count_accounts_for_user(uid)
    can, reason = can_add_business(row, n_accounts)
    if not can:
        return jsonify({"error": reason}), 403

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

    cap = max_posts_per_day_for_plan(effective_plan_code(row))
    ppd = 3
    try:
        ppd = int(data.get("posts_per_day") or 3)
    except (TypeError, ValueError):
        ppd = 3
    ppd = max(1, min(3, ppd, cap))

    account_id = db.add_account(
        platform=fields["platform"],
        page_url=fields["page_url"],
        business_url=fields["business_url"],
        business_name=fields["business_name"],
        user_id=uid,
        session_data=None,
        posts_per_day=ppd,
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
@login_required
def delete_account(account_id):
    """Remove a linked account"""
    if not db.get_account(account_id, current_user.id):
        return jsonify({"error": "Account not found"}), 404
    db.delete_account(account_id, user_id=current_user.id)
    return jsonify({"message": "Account removed"})


@app.route("/api/accounts/<int:account_id>/posts-per-day", methods=["PATCH"])
@login_required
def patch_account_posts_per_day(account_id):
    data = request.get_json(silent=True) or {}
    row = db.get_user_by_id(current_user.id)
    cap = max_posts_per_day_for_plan(effective_plan_code(row))
    try:
        want = int(data.get("posts_per_day") or 3)
    except (TypeError, ValueError):
        want = 3
    want = max(1, min(3, want, cap))
    if not db.update_account_posts_per_day(account_id, current_user.id, want):
        return jsonify({"error": "Account not found"}), 404
    return jsonify({"ok": True, "posts_per_day": want})


@app.route("/api/accounts/<int:account_id>/weekly-approval", methods=["POST"])
@login_required
def weekly_approval_route(account_id):
    data = request.get_json(silent=True) or {}
    approved = bool(data.get("approved"))
    if not db.set_weekly_approval(account_id, current_user.id, approved):
        return jsonify({"error": "Account not found"}), 404
    acc = db.get_account(account_id, current_user.id)
    return jsonify(
        {
            "ok": True,
            "week_approval_active": account_week_approved_for_current_week(acc or {}),
            "weekly_approved_iso_week": (acc or {}).get("weekly_approved_iso_week"),
        }
    )


@app.route("/api/accounts/<int:account_id>/playwright-storage", methods=["GET"])
@login_required
def playwright_storage_status(account_id):
    """Whether headless posting has session data (uploaded JSON and/or Chromium profile on disk)."""
    if not db.get_account(account_id, current_user.id):
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
@login_required
def playwright_storage_save(account_id):
    """Save Playwright storage_state JSON (cookies + origins) for headless cloud posting."""
    if not db.get_account(account_id, current_user.id):
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
@login_required
def playwright_storage_clear(account_id):
    """Remove uploaded Playwright storage for this account."""
    if not db.get_account(account_id, current_user.id):
        return jsonify({"error": "Account not found"}), 404
    path = _playwright_storage_file(account_id)
    if path.is_file():
        path.unlink()
    return jsonify({"message": "Session cleared", "has_session": False})


@app.route("/api/accounts/<int:account_id>/facebook/disconnect", methods=["POST"])
@login_required
def facebook_graph_disconnect(account_id):
    """Clear Facebook Graph API tokens (use Connect again to re-authorize)."""
    if not db.get_account(account_id, current_user.id):
        return jsonify({"error": "Account not found"}), 404
    db.clear_facebook_graph_token(account_id)
    return jsonify({"message": "Facebook disconnected", "fb_graph_connected": False})


@app.route("/api/facebook/oauth/start", methods=["GET"])
@login_required
def facebook_oauth_start():
    """Redirect to Meta login; after approval, user returns to /api/facebook/oauth/callback."""
    app_id = (os.getenv("FACEBOOK_APP_ID") or "").strip()
    secret = (os.getenv("FACEBOOK_APP_SECRET") or "").strip()
    if not app_id or not secret:
        return jsonify(
            {
                "error": (
                    "Facebook Login is not configured. Set FACEBOOK_APP_ID and FACEBOOK_APP_SECRET "
                    "in Railway (see DEPLOY.md)."
                )
            }
        ), 503
    sch, host = _forwarded_scheme_host()
    if not facebook_graph.facebook_effective_redirect_uri(
        forwarded_scheme=sch,
        forwarded_host=host,
    ):
        want = _suggested_facebook_callback_url()
        return jsonify(
            {
                "error": (
                    "Could not build your Facebook callback URL. Open this app using your public https link "
                    f"(not an IP address). Then add this exact line in Meta → Facebook Login → Valid OAuth Redirect URIs: {want}"
                )
            }
        ), 503
    account_id = request.args.get("account_id", type=int)
    if not account_id:
        return jsonify({"error": "account_id query parameter is required"}), 400
    if not db.get_account(account_id, current_user.id):
        return jsonify({"error": "Account not found"}), 404
    try:
        url = facebook_graph.oauth_authorize_url(
            account_id,
            app.secret_key,
            forwarded_scheme=sch,
            forwarded_host=host,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 503
    return redirect(url)


@app.route("/api/facebook/oauth/callback", methods=["GET"])
def facebook_oauth_callback():
    """Meta redirects here with ?code=&state= — exchange token and store Page access token."""
    base_dash = _public_app_base().rstrip("/") + "/dashboard"
    err = request.args.get("error_description") or request.args.get("error")
    if err:
        return redirect(f"{base_dash}?fb_error={quote(err[:500])}")
    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state:
        return redirect(f"{base_dash}?fb_error={quote('Missing authorization code')}")
    try:
        aid, redirect_uri = facebook_graph.parse_oauth_state(state, app.secret_key)
    except SignatureExpired:
        return redirect(
            f"{base_dash}?fb_error={quote('Session expired — open Accounts and click Connect Facebook again')}"
        )
    except BadSignature:
        return redirect(f"{base_dash}?fb_error={quote('Invalid OAuth state')}")
    except ValueError as e:
        return redirect(f"{base_dash}?fb_error={quote(str(e))}")
    ok, msg = facebook_graph.complete_oauth_and_store(db, aid, code, redirect_uri=redirect_uri)
    if ok:
        return redirect(f"{base_dash}?fb_connected=1")
    return redirect(f"{base_dash}?fb_error={quote(msg or 'Facebook connection failed')}")


# ─── CRAWL ROUTES ────────────────────────────────────────────────────────────


@app.route("/api/crawl/<int:account_id>", methods=["POST"])
@login_required
def recrawl(account_id):
    """Re-crawl the business website to refresh content"""
    account = db.get_account(account_id, current_user.id)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    result = crawler.crawl(account["business_url"])
    db.update_crawl_data(account_id, result)
    return jsonify({"message": "Crawl complete", "pages_found": result.get("pages_count", 0)})


# ─── POST QUEUE ROUTES ───────────────────────────────────────────────────────


@app.route("/api/queue", methods=["GET"])
@login_required
def get_queue():
    """Return today's post queue for the current user."""
    posts = db.get_todays_queue(current_user.id)
    return jsonify({"posts": posts})


@app.route("/api/queue/generate", methods=["POST"])
@login_required
def generate_posts():
    """Generate AI posts for today for the current user's accounts."""
    uid = current_user.id
    user_row = db.get_user_by_id(uid)
    data = request.json or {}
    account_id = data.get("account_id")

    if account_id:
        accounts = [db.get_account(int(account_id), uid)]
    else:
        accounts = db.get_accounts_for_user(uid)

    generated = []
    for account in accounts:
        if not account:
            continue
        n = effective_posts_per_day_for_account(user_row, account)
        crawl_data = db.get_crawl_data(account["id"])
        recent = db.get_recent_history_captions(account["id"], 30)
        posts = ai_gen.generate_daily_posts(
            business_name=account["business_name"],
            business_url=account["business_url"],
            platform=account["platform"],
            crawl_data=crawl_data,
            recent_published_captions=recent,
            num_posts=n,
        )
        for post in posts:
            post_id = db.add_post(
                account_id=account["id"],
                caption=post["caption"],
                post_type=post["type"],
                scheduled_time=post["scheduled_time"],
                image_prompt=post.get("image_prompt", ""),
                user_id=uid,
            )
            generated.append(
                {"id": post_id, "type": post["type"], "account": account["business_name"]}
            )

        if user_row and account_week_approved_for_current_week(account):
            pending = db.get_todays_pending_for_account(int(account["id"]))
            for p in pending[:n]:
                ok, err, _m = publish_post_with_deps(db, poster, _post_executor, int(p["id"]))
                if ok:
                    logger.info("Auto-published post_id=%s after manual generate", p["id"])
                else:
                    logger.warning("Auto-publish failed post_id=%s: %s", p["id"], err[:200])

    return jsonify({"generated": generated, "count": len(generated)})


@app.route("/api/queue/<int:post_id>", methods=["GET"])
@login_required
def get_post(post_id):
    """Get a specific post from the queue"""
    post = db.get_post(post_id, current_user.id)
    if not post:
        return jsonify({"error": "Post not found — refresh the queue."}), 404
    return jsonify(post)


@app.route("/api/queue/<int:post_id>", methods=["PUT"])
@login_required
def update_post(post_id):
    """Edit a post caption before publishing"""
    if not db.get_post(post_id, current_user.id):
        return jsonify({"error": "Post not found"}), 404
    data = request.json
    db.update_post_caption(post_id, data.get("caption", ""), user_id=current_user.id)
    return jsonify({"message": "Post updated"})


@app.route("/api/queue/<int:post_id>", methods=["DELETE"])
@login_required
def delete_post(post_id):
    """Delete a post from the queue"""
    if not db.get_post(post_id, current_user.id):
        return jsonify({"error": "Post not found"}), 404
    db.delete_post(post_id, user_id=current_user.id)
    return jsonify({"message": "Post deleted"})


# ─── POSTING ROUTES ──────────────────────────────────────────────────────────


@app.route("/api/post/<int:post_id>", methods=["POST"])
@login_required
def post_now(post_id):
    """User-triggered publish (Facebook Graph or Playwright)."""
    post = db.get_post(post_id, current_user.id)
    if not post:
        return jsonify(
            {
                "error": (
                    "Post not found — it may have been deleted, or the server has a different "
                    "database than when this page loaded. Refresh the queue and try again."
                )
            }
        ), 404

    timeout_s = int(os.getenv("POST_TIMEOUT_SECONDS", "840"))
    ok, err, method = publish_post_with_deps(
        db, poster, _post_executor, post_id, timeout_s=timeout_s
    )
    if ok:
        return jsonify(
            {
                "message": "Posted successfully",
                "post_id": post_id,
                "posting_headless": bool(poster.headless),
                "method": method or "unknown",
            }
        )
    status = 500
    if err and ("longer than" in err or "timed out" in err.lower()):
        status = 504
    return jsonify({"error": err or "Posting failed"}), status


# ─── ANALYTICS ROUTES ────────────────────────────────────────────────────────


@app.route("/api/analytics", methods=["GET"])
@login_required
def get_analytics():
    """Return post performance analytics"""
    uid = current_user.id
    stats = db.get_analytics(uid)
    stats["recent_posts"] = db.get_recent_published_all(40, user_id=uid)
    return jsonify(stats)


@app.route("/api/analytics/<int:account_id>", methods=["GET"])
@login_required
def get_account_analytics(account_id):
    """Return analytics for a specific account"""
    if not db.get_account(account_id, current_user.id):
        return jsonify({"error": "Account not found"}), 404
    stats = db.get_account_analytics(account_id, user_id=current_user.id)
    return jsonify(stats)


# ─── SCHEDULER ROUTES ────────────────────────────────────────────────────────


@app.route("/api/scheduler/start", methods=["POST"])
@login_required
def start_scheduler():
    """Start the automatic post generation scheduler"""
    scheduler.start()
    return jsonify({"message": "Scheduler started"})


@app.route("/api/scheduler/stop", methods=["POST"])
@login_required
def stop_scheduler():
    """Stop the scheduler"""
    scheduler.stop()
    return jsonify({"message": "Scheduler stopped"})


@app.route("/api/scheduler/status", methods=["GET"])
@login_required
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
            **_facebook_api_extras(),
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
