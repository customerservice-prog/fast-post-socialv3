#!/usr/bin/env python3
"""
Verify /api/dashboard and /api/health expose posting_headless (cloud UX contract).
Run from repo root: python scripts/verify_api_posting_flags.py
"""
from __future__ import annotations

import os
import sys


def main() -> None:
    backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
    sys.path.insert(0, backend)
    os.chdir(backend)

    import app as app_module

    flask_app = app_module.app
    with flask_app.test_client() as c:
        r = c.get("/api/dashboard")
        assert r.status_code == 200, r.data
        d = r.get_json()
        assert "posting_headless" in d, d.keys()
        assert isinstance(d["posting_headless"], bool)

        r2 = c.get("/api/health")
        assert r2.status_code == 200
        h = r2.get_json()
        assert h.get("posting_headless") is d["posting_headless"]
        assert "facebook_oauth_configured" in h
        assert isinstance(h["facebook_oauth_configured"], bool)
        assert d.get("facebook_oauth_configured") == h["facebook_oauth_configured"]

    print("verify_api_posting_flags: ok")


if __name__ == "__main__":
    main()
