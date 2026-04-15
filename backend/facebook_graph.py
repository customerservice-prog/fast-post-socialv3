"""
Facebook Graph API: OAuth (Facebook Login) + Page feed posts.
Lets Railway/headless posting work without Playwright session files — user clicks
"Connect Facebook" once, then Post now uses the stored Page access token.

Requires a Meta app: https://developers.facebook.com/ — add FACEBOOK_* env vars.
"""

from __future__ import annotations

import io
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


REDIRECT_PATH_SUFFIX = "/api/facebook/oauth/callback"

# OAuth redirect must be YOUR site (e.g. socialautopost.online), never facebook.com / fb.com (common mistake: pasting a Page URL).
_FORBIDDEN_OAUTH_CALLBACK_SUFFIXES = (
    "facebook.com",
    "fb.com",
    "fbcdn.net",
)


def _hostname_must_not_be_meta_platform(host: str) -> bool:
    """True if hostname is forbidden as redirect_uri host (facebook.com, fb.com, etc.)."""
    h = (host or "").lower().strip(".")
    if not h:
        return True
    for suf in _FORBIDDEN_OAUTH_CALLBACK_SUFFIXES:
        if h == suf or h.endswith("." + suf):
            return True
    return False


def _redirect_uri_is_valid_public_callback(u: str) -> bool:
    """True if u is a safe, non-Facebook callback URL ending with our path."""
    from urllib.parse import urlparse

    u = (u or "").strip().rstrip("/")
    if not u:
        return False
    p = urlparse(u)
    host = (p.hostname or "").lower()
    if not host or _hostname_must_not_be_meta_platform(host):
        return False
    # profile.php in path is never our FastPost callback
    if "profile.php" in (p.path or "").lower():
        return False
    return u.endswith(REDIRECT_PATH_SUFFIX.rstrip("/"))


def _assert_safe_oauth_redirect_uri(redirect: str, context: str) -> None:
    """Refuse to send token exchange or dialog with a Meta-owned redirect (misconfigured env)."""
    from urllib.parse import urlparse

    r = (redirect or "").strip()
    if not r or not _redirect_uri_is_valid_public_callback(r):
        p = urlparse(r)
        logger.error(
            "[Facebook OAuth] Blocked unsafe redirect_uri (%s): %s",
            context,
            r[:200],
        )
        raise ValueError(
            "OAuth redirect_uri is misconfigured. In Railway set PUBLIC_APP_URL=https://your-domain "
            "(your FastPost site, e.g. https://socialautopost.online) — never a facebook.com profile/Page link. "
            "Unset FACEBOOK_REDIRECT_URI or set it to https://your-domain/api/facebook/oauth/callback"
        )


def redirect_uri_from_forwarded(
    forwarded_scheme: Optional[str],
    forwarded_host: Optional[str],
) -> Optional[str]:
    """
    Build callback URL from reverse-proxy headers (Railway, etc.) when env vars are unset.
    Meta must list this exact URL under Valid OAuth Redirect URIs.
    """
    if not forwarded_scheme or not forwarded_host:
        return None
    scheme = forwarded_scheme.strip().lower().split(",", 1)[0].strip()
    host = forwarded_host.strip().split(",", 1)[0].strip()
    host_only = host.split(":")[0]
    if not host_only or _hostname_must_not_be_meta_platform(host_only):
        return None
    if scheme not in ("http", "https"):
        scheme = "https"
    hl = host.lower()
    is_local = (
        hl.startswith("localhost")
        or hl.startswith("127.0.0.1")
        or hl.startswith("[::1]")
    )
    if not is_local and scheme == "http":
        scheme = "https"
    base = f"{scheme}://{host}".rstrip("/")
    candidate = f"{base}{REDIRECT_PATH_SUFFIX}"
    if _redirect_uri_is_valid_public_callback(candidate):
        return candidate.rstrip("/")
    return None


def _facebook_effective_redirect_uri_from_env() -> Optional[str]:
    """
    redirect_uri from env only (PUBLIC_APP_URL, FACEBOOK_REDIRECT_URI, RAILWAY_PUBLIC_DOMAIN).
    """
    from urllib.parse import urlparse

    explicit = (os.getenv("FACEBOOK_REDIRECT_URI") or "").strip()
    if explicit and _redirect_uri_is_valid_public_callback(explicit):
        return explicit.rstrip("/")

    def _origin_only(url: str) -> str:
        u = (url or "").strip()
        if not u:
            return ""
        if "://" not in u:
            u = "https://" + u
        p = urlparse(u)
        if p.scheme not in ("http", "https") or not p.netloc:
            return ""
        host = (p.hostname or "").lower()
        if _hostname_must_not_be_meta_platform(host):
            return ""
        return f"{p.scheme}://{p.netloc}".rstrip("/")

    pub_raw = (os.getenv("PUBLIC_APP_URL") or "").strip()
    pub = _origin_only(pub_raw)
    if pub_raw and not pub:
        logger.error(
            "[Facebook OAuth] PUBLIC_APP_URL looks like a Meta URL or is invalid (%s). "
            "Use your app origin only, e.g. https://socialautopost.online",
            pub_raw[:160],
        )
    if not pub:
        rdom = (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
        if rdom and not _hostname_must_not_be_meta_platform(rdom.split("/")[0].split(":")[0]):
            rdom = rdom.split("/")[0].split(":")[0]
            if rdom:
                pub = f"https://{rdom}"
    if pub:
        derived = f"{pub}{REDIRECT_PATH_SUFFIX}"
        if _redirect_uri_is_valid_public_callback(derived):
            return derived.rstrip("/")

    return None


def facebook_effective_redirect_uri(
    forwarded_scheme: Optional[str] = None,
    forwarded_host: Optional[str] = None,
) -> Optional[str]:
    """
    redirect_uri sent to Meta (must match Valid OAuth Redirect URIs exactly).
    Prefers env vars; if missing, uses Host / X-Forwarded-* from the live request (Railway).
    """
    env_uri = _facebook_effective_redirect_uri_from_env()
    if env_uri:
        return env_uri
    return redirect_uri_from_forwarded(forwarded_scheme, forwarded_host)


def facebook_redirect_uri_valid() -> bool:
    """True if env vars alone yield a valid OAuth redirect (no request needed)."""
    return _facebook_effective_redirect_uri_from_env() is not None


def facebook_oauth_configured() -> bool:
    """Meta app id + secret — redirect URL can come from this request's hostname on Railway."""
    return bool(
        os.getenv("FACEBOOK_APP_ID", "").strip()
        and os.getenv("FACEBOOK_APP_SECRET", "").strip()
    )


def log_facebook_oauth_env_warnings() -> None:
    """Warn if env will break Meta OAuth (https, path, host). Call once at startup."""
    uri = (os.getenv("FACEBOOK_REDIRECT_URI") or "").strip()
    if uri:
        from urllib.parse import urlparse

        p = urlparse(uri)
        host = (p.hostname or "").lower()
        if _hostname_must_not_be_meta_platform(host):
            logger.error(
                "FACEBOOK_REDIRECT_URI must NOT be a facebook.com URL (you may have pasted a profile/Page link). "
                "Unset it and use PUBLIC_APP_URL=https://your-site or set a correct redirect. "
                "Effective OAuth redirect will try PUBLIC_APP_URL + %s if set.",
                REDIRECT_PATH_SUFFIX,
            )
        else:
            is_local = host in ("localhost", "127.0.0.1", "::1")
            if p.scheme == "http" and not is_local:
                logger.warning(
                    "FACEBOOK_REDIRECT_URI uses http:// — use https:// in production (and in Meta).",
                )
            normalized = uri.rstrip("/")
            if not normalized.endswith(REDIRECT_PATH_SUFFIX):
                logger.warning(
                    "FACEBOOK_REDIRECT_URI should end with %s (current: %s)",
                    REDIRECT_PATH_SUFFIX,
                    uri[:200],
                )

    eff = _facebook_effective_redirect_uri_from_env()
    if eff and not (os.getenv("FACEBOOK_REDIRECT_URI") or "").strip():
        logger.info("[Facebook OAuth] Redirect URI from environment: %s", eff)
    elif not eff and (os.getenv("FACEBOOK_APP_ID") or "").strip():
        logger.info(
            "[Facebook OAuth] No PUBLIC_APP_URL/RAILWAY_PUBLIC_DOMAIN; redirect URL will be built from each request's Host (add that exact URL in Meta → Valid OAuth Redirect URIs).",
        )


def _norm_secret(secret) -> str:
    if isinstance(secret, (bytes, bytearray)):
        return bytes(secret).decode("utf-8", "replace")
    return str(secret)


def _serializer(secret) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_norm_secret(secret), salt="fastpost-fb-oauth-v1")


def sign_oauth_state(account_id: int, redirect_uri: str, secret) -> str:
    return _serializer(secret).dumps(
        {"account_id": int(account_id), "redirect_uri": str(redirect_uri).strip()}
    )


def parse_oauth_state(state: str, secret, max_age: int = 3600) -> Tuple[int, str]:
    data = _serializer(secret).loads(state, max_age=max_age)
    aid = int(data["account_id"])
    ruri = data.get("redirect_uri")
    if isinstance(ruri, str) and ruri.strip():
        ruri = ruri.strip()
        if not _redirect_uri_is_valid_public_callback(ruri):
            raise ValueError(
                "OAuth session used an invalid redirect — remove bad PUBLIC_APP_URL/FACEBOOK_REDIRECT_URI in Railway, "
                "then click Connect Facebook again"
            )
        return aid, ruri
    legacy = facebook_effective_redirect_uri()
    if legacy:
        return aid, legacy
    raise ValueError("OAuth state missing redirect — click Connect Facebook again")


def oauth_authorize_url(
    account_id: int,
    signing_secret,
    forwarded_scheme: Optional[str] = None,
    forwarded_host: Optional[str] = None,
) -> str:
    app_id = (os.getenv("FACEBOOK_APP_ID") or "").strip()
    if not app_id:
        raise ValueError("FACEBOOK_APP_ID is not set")
    redirect = facebook_effective_redirect_uri(
        forwarded_scheme=forwarded_scheme,
        forwarded_host=forwarded_host,
    )
    if not redirect:
        raise ValueError("No OAuth redirect URI (open this app from its public https URL or set PUBLIC_APP_URL)")
    _assert_safe_oauth_redirect_uri(redirect, "oauth_authorize_url")
    logger.info("[Facebook OAuth] Authorize redirect_uri=%s account_id=%s", redirect, account_id)
    state = sign_oauth_state(account_id, redirect, signing_secret)
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


def _norm_name(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _names_loosely_match(a: str, b: str) -> bool:
    """Match business name to Page name when URLs differ (e.g. Instagram link in account)."""
    x, y = _norm_name(a), _norm_name(b)
    if not x or not y:
        return False
    if x == y:
        return True
    if len(x) >= 4 and (x in y or y in x):
        return True
    return x.split()[0] == y.split()[0] and len(x.split()[0]) >= 4


def match_page_to_account(
    pages: List[Dict[str, Any]],
    account_page_url: str,
    business_name: str = "",
) -> Optional[Dict[str, Any]]:
    """Pick the Page dict that matches the linked account page_url (and optionally business name)."""
    if not pages:
        return None
    target = normalize_fb_path(account_page_url)
    if not target:
        if len(pages) == 1:
            return pages[0]
        # Instagram-only / empty URL: pick Page whose name matches business
        if business_name.strip():
            for p in pages:
                if _names_loosely_match(business_name, p.get("name") or ""):
                    return p
        return None

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

    # Multiple Pages, URL didn't match — try business name vs Page name
    if business_name.strip():
        for p in pages:
            if _names_loosely_match(business_name, p.get("name") or ""):
                return p

    return pages[0] if len(pages) == 1 else None


def post_page_feed(page_id: str, page_access_token: str, message: str) -> Tuple[bool, str]:
    """POST /{page-id}/feed — publish text-only post (legacy)."""
    data, status = _post(
        f"/{page_id}/feed",
        {"message": message, "access_token": page_access_token},
    )
    if status == 200 and data.get("id"):
        return True, ""
    err = data.get("error") or {}
    msg = err.get("message") or str(data)[:500]
    return False, msg


def post_page_photo(
    page_id: str,
    page_access_token: str,
    message: str,
    image_bytes: bytes,
    filename: str = "fastpost-share.jpg",
    mime: str = "image/jpeg",
) -> Tuple[bool, str]:
    """
    POST /{page-id}/photos — publish photo with caption (multipart).
    https://developers.facebook.com/docs/graph-api/reference/page/photos/
    access_token is passed as a query param (reliable with multipart on many hosts).
    """
    url = f"{GRAPH_BASE}/{page_id}/photos"
    buf = io.BytesIO(image_bytes)
    buf.seek(0)
    files = {
        "source": (filename, buf, mime),
    }
    data = {"message": (message or "")[:63206]}  # FB message length safety
    params = {"access_token": page_access_token, "published": "true"}
    try:
        r = requests.post(url, params=params, files=files, data=data, timeout=120)
    except requests.RequestException as e:
        return False, str(e)[:500]
    try:
        j = r.json()
    except Exception:
        return False, (r.text or "")[:500]
    if r.status_code == 200 and j.get("id"):
        return True, ""
    err = j.get("error") or {}
    msg = err.get("message") or str(j)[:500]
    return False, msg


def complete_oauth_and_store(
    db,
    account_id: int,
    code: str,
    redirect_uri: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Exchange code, get long-lived user token, find Page matching account page_url, save Page token.
    """
    redirect_uri = (redirect_uri or "").strip() or facebook_effective_redirect_uri()
    if not redirect_uri:
        return False, "OAuth redirect URI missing — set PUBLIC_APP_URL or open the app from your live site URL"
    try:
        _assert_safe_oauth_redirect_uri(redirect_uri, "complete_oauth_and_store")
    except ValueError as e:
        return False, str(e)
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
    picked = match_page_to_account(
        pages,
        page_url,
        (account.get("business_name") or "").strip(),
    )
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
