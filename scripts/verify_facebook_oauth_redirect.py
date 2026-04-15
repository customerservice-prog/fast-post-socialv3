#!/usr/bin/env python3
"""
Regression: OAuth authorize URL must use facebook_effective_redirect_uri() (same as token exchange).
Run: python scripts/verify_facebook_oauth_redirect.py
"""
from __future__ import annotations

import os
import sys
from urllib.parse import parse_qs, urlparse, unquote


def main() -> None:
    backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
    sys.path.insert(0, backend)

    for k in list(os.environ.keys()):
        if k.startswith("FACEBOOK_") or k in ("PUBLIC_APP_URL", "RAILWAY_PUBLIC_DOMAIN"):
            del os.environ[k]

    os.environ["FACEBOOK_APP_ID"] = "123456789"
    os.environ["FACEBOOK_APP_SECRET"] = "dummysecret"
    os.environ["PUBLIC_APP_URL"] = "https://example.com"

    import facebook_graph as fg

    eff = fg.facebook_effective_redirect_uri()
    assert eff == "https://example.com/api/facebook/oauth/callback", eff

    url = fg.oauth_authorize_url(42, "unit-test-signing-secret-32chars!!")
    qs = parse_qs(urlparse(url).query)
    redir = unquote(qs["redirect_uri"][0])
    assert redir == eff, f"authorize redirect {redir!r} != effective {eff!r}"

    # Invalid explicit URI must not override PUBLIC_APP_URL
    os.environ["FACEBOOK_REDIRECT_URI"] = "https://www.facebook.com/profile.php?id=1"
    eff2 = fg.facebook_effective_redirect_uri()
    assert eff2 == "https://example.com/api/facebook/oauth/callback", eff2

    url2 = fg.oauth_authorize_url(42, "unit-test-signing-secret-32chars!!")
    qs2 = parse_qs(urlparse(url2).query)
    assert unquote(qs2["redirect_uri"][0]) == eff2

    # PUBLIC_APP_URL with accidental path — use origin only
    os.environ.pop("FACEBOOK_REDIRECT_URI", None)
    os.environ["PUBLIC_APP_URL"] = "https://example.com/dashboard"
    eff_path = fg.facebook_effective_redirect_uri()
    assert eff_path == "https://example.com/api/facebook/oauth/callback", eff_path

    # Railway hostname only
    os.environ.pop("PUBLIC_APP_URL", None)
    os.environ.pop("FACEBOOK_REDIRECT_URI", None)
    os.environ["RAILWAY_PUBLIC_DOMAIN"] = "myapp.up.railway.app"
    eff3 = fg.facebook_effective_redirect_uri()
    assert eff3 == "https://myapp.up.railway.app/api/facebook/oauth/callback", eff3

    # No env public URL — derive from request Host (Railway without PUBLIC_APP_URL)
    os.environ.pop("RAILWAY_PUBLIC_DOMAIN", None)
    assert fg.facebook_effective_redirect_uri() is None
    eff4 = fg.facebook_effective_redirect_uri(
        forwarded_scheme="https",
        forwarded_host="my-service.up.railway.app",
    )
    assert eff4 == "https://my-service.up.railway.app/api/facebook/oauth/callback", eff4
    url4 = fg.oauth_authorize_url(
        7,
        "unit-test-signing-secret-32chars!!",
        forwarded_scheme="https",
        forwarded_host="my-service.up.railway.app",
    )
    qs4 = parse_qs(urlparse(url4).query)
    assert unquote(qs4["redirect_uri"][0]) == eff4
    aid, ruri = fg.parse_oauth_state(qs4["state"][0], "unit-test-signing-secret-32chars!!")
    assert aid == 7 and ruri == eff4

    print("verify_facebook_oauth_redirect: ok")


if __name__ == "__main__":
    main()
