"""
Facebook Graph API: OAuth (Facebook Login) + Page feed posts.
Lets Railway/headless posting work without Playwright session files — user clicks
"Connect Facebook" once, then Post now uses the stored Page access token.

Requires a Meta app: https://developers.facebook.com/ — add FACEBOOK_* env vars.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

GRAPH_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
FB_DIALOG = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"

# pages_show_list: list Pages; pages_manage_posts: publish to Page feed
DEFAULT_SCOPES = "pages_show_list,pages_manage_posts"


def facebook_oauth_configured() -> bool:
    return bool(
        os.getenv("FACEBOOK_APP_ID", "").strip()
        and os.getenv("FACEBOOK_APP_SECRET", "").strip()
        and os.getenv("FACEBOOK_REDIRECT_URI", "").strip()
    )


def log_facebook_oauth_env_warnings() -> None:
    """Warn if env will break Meta OAuth (https, path). Call once at startup."""
    uri = (os.getenv("FACEBOOK_REDIRECT_URI") or "").strip()
    if not uri:
        return
    from urllib.parse import urlparse

    p = urlparse(uri)
    host = (p.hostname or "").lower()
    is_local = host in ("localhost", "127.0.0.1", "::1")
    if p.scheme == "http" and not is_local:
        logger.warning(
            "FACEBOOK_REDIRECT_URI uses http:// — Facebook requires https:// for production. "
            "Set FACEBOOK_REDIRECT_URI=https://%s%s (and the same URL in Meta → Valid OAuth Redirect URIs).",
            host,
            p.path or "/api/facebook/oauth/callback",
        )
    suffix = "/api/facebook/oauth/callback"
    normalized = uri.rstrip("/")
    if not normalized.endswith(suffix):
        logger.warning(
            "FACEBOOK_REDIRECT_URI should end with %s (current: %s)",
            suffix,
            uri[:200],
        )


def _norm_secret(secret) -> str:
    if isinstance(secret, (bytes, bytearray)):
        return bytes(secret).decode("utf-8", "replace")
    return str(secret)


def _serializer(secret) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_norm_secret(secret), salt="fastpost-fb-oauth-v1")


def sign_oauth_state(account_id: int, secret) -> str:
    return _serializer(secret).dumps({"account_id": int(account_id)})


def parse_oauth_state(state: str, secret, max_age: int = 3600) -> int:
    data = _serializer(secret).loads(state, max_age=max_age)
    return int(data["account_id"])


def oauth_authorize_url(account_id: int, signing_secret) -> str:
    app_id = os.environ["FACEBOOK_APP_ID"].strip()
    redirect = os.environ["FACEBOOK_REDIRECT_URI"].strip()
    state = sign_oauth_state(account_id, signing_secret)
    q = (
        f"client_id={quote(app_id)}"
        f"&redirect_uri={quote(redirect, safe='')}"
        f"&state={quote(state)}"
        f"&scope={quote(DEFAULT_SCOPES)}"
        "&response_type=code"
    )
    return f"{FB_DIALOG}?{q}"


def _get(path: str, params: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    r = requests.get(f"{GRAPH_BASE}{path}", params=params, timeout=45)
    try:
        data = r.json()
    except Exception:
        data = {"error": {"message": r.text[:400]}}
    return data, r.status_code


def _post(path: str, data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    r = requests.post(f"{GRAPH_BASE}{path}", data=data, timeout=60)
    try:
        j = r.json()
    except Exception:
        j = {"error": {"message": r.text[:400]}}
    return j, r.status_code


def exchange_code_for_user_token(code: str, redirect_uri: str) -> Tuple[Optional[str], str]:
    app_id = os.environ["FACEBOOK_APP_ID"].strip()
    secret = os.environ["FACEBOOK_APP_SECRET"].strip()
    data, status = _get(
        "/oauth/access_token",
        {
            "client_id": app_id,
            "client_secret": secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
    )
    if status != 200 or not data.get("access_token"):
        err = (data.get("error") or {}).get("message") or str(data)
        return None, err
    return data["access_token"], ""


def exchange_long_lived_user_token(short_lived: str) -> Tuple[Optional[str], str]:
    app_id = os.environ["FACEBOOK_APP_ID"].strip()
    secret = os.environ["FACEBOOK_APP_SECRET"].strip()
    data, status = _get(
        "/oauth/access_token",
        {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": secret,
            "fb_exchange_token": short_lived,
        },
    )
    if status != 200 or not data.get("access_token"):
        err = (data.get("error") or {}).get("message") or str(data)
        return None, err
    return data["access_token"], ""


def fetch_managed_pages(user_access_token: str) -> Tuple[List[Dict[str, Any]], str]:
    data, status = _get(
        "/me/accounts",
        {"fields": "id,name,access_token,link", "access_token": user_access_token},
    )
    if status != 200:
        err = (data.get("error") or {}).get("message") or str(data)
        return [], err
    return list(data.get("data") or []), ""


def normalize_fb_path(url: str) -> str:
    if not url:
        return ""
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = u.replace("www.facebook.com/", "facebook.com/")
    u = u.replace("m.facebook.com/", "facebook.com/")
    if "profile.php" in u:
        m = re.search(r"[?&]id=(\d+)", url, re.I)
        if m:
            return f"profile_id:{m.group(1)}"
    if "facebook.com/" not in u:
        return ""
    path = u.split("facebook.com/", 1)[-1]
    return path.split("?")[0].strip("/")


def match_page_to_account(pages: List[Dict[str, Any]], account_page_url: str) -> Optional[Dict[str, Any]]:
    """Pick the Page dict that matches the linked account page_url."""
    if not pages:
        return None
    target = normalize_fb_path(account_page_url)
    if not target:
        return pages[0] if len(pages) == 1 else None

    for p in pages:
        link = p.get("link") or ""
        if normalize_fb_path(link) == target:
            return p
    # suffix match: page slug in URL
    tail = target.split("/")[-1]
    for p in pages:
        link = normalize_fb_path(p.get("link") or "")
        if tail and (tail in link or link.endswith(tail)):
            return p
        pid = str(p.get("id") or "")
        if pid and pid in account_page_url:
            return p

    return pages[0] if len(pages) == 1 else None


def post_page_feed(page_id: str, page_access_token: str, message: str) -> Tuple[bool, str]:
    """POST /{page-id}/feed — publish text post to the Page."""
    data, status = _post(
        f"/{page_id}/feed",
        {"message": message, "access_token": page_access_token},
    )
    if status == 200 and data.get("id"):
        return True, ""
    err = data.get("error") or {}
    msg = err.get("message") or str(data)[:500]
    return False, msg


def complete_oauth_and_store(
    db,
    account_id: int,
    code: str,
) -> Tuple[bool, str]:
    """
    Exchange code, get long-lived user token, find Page matching account page_url, save Page token.
    """
    redirect_uri = os.environ["FACEBOOK_REDIRECT_URI"].strip()
    short_tok, err = exchange_code_for_user_token(code, redirect_uri)
    if not short_tok:
        return False, err or "Token exchange failed"

    long_tok, err = exchange_long_lived_user_token(short_tok)
    user_token = long_tok or short_tok
    if not user_token:
        return False, err or "Long-lived token failed"

    pages, err = fetch_managed_pages(user_token)
    if err:
        return False, err
    if not pages:
        return False, "No Facebook Pages found. Use an account that manages at least one Page."

    account = db.get_account(account_id)
    if not account:
        return False, "Account not found"
    page_url = account.get("page_url") or ""
    picked = match_page_to_account(pages, page_url)
    if not picked:
        names = ", ".join(p.get("name", "?") for p in pages[:8])
        return (
            False,
            f"Could not match your linked Page URL to a Page you manage. "
            f"Update the Page URL in Accounts to match one of: {names}",
        )

    pid = str(picked["id"])
    tok = picked.get("access_token") or ""
    if not tok:
        return False, "Page access token missing — reauthorize with pages_manage_posts."

    exp = None  # Page tokens from /me/accounts are long-lived; optional expiry not always returned
    db.update_facebook_graph_token(account_id, pid, tok, exp)
    logger.info("Facebook Graph token stored for account_id=%s page_id=%s", account_id, pid)
    return True, ""
