#!/usr/bin/env python3
"""
Print FACEBOOK_* lines to paste into Railway (HTTPS). Meta must use the same redirect URI.

Usage:
  python scripts/print_railway_facebook_vars.py
  python scripts/print_railway_facebook_vars.py socialautopost.online
"""
from __future__ import annotations

import re
import sys


def main() -> None:
    raw = (sys.argv[1] if len(sys.argv) > 1 else "socialautopost.online").strip()
    raw = re.sub(r"^https?://", "", raw)
    domain = raw.split("/")[0].strip()
    if not domain:
        print("Usage: python scripts/print_railway_facebook_vars.py [your-domain.com]", file=sys.stderr)
        sys.exit(1)
    base = f"https://{domain}"
    callback = f"{base}/api/facebook/oauth/callback"
    print("# Paste into Railway -> Variables (add FACEBOOK_APP_ID and FACEBOOK_APP_SECRET from Meta):")
    print(f"FACEBOOK_REDIRECT_URI={callback}")
    print(f"PUBLIC_APP_URL={base}")
    print()
    print("# Meta: App settings -> Basic -> App domains (hostname only, no https):")
    print(domain)
    print()
    print("# Meta: Facebook Login -> Settings -> Valid OAuth Redirect URIs (one line, must match exactly):")
    print(callback)


if __name__ == "__main__":
    main()
