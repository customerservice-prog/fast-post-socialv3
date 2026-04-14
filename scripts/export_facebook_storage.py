"""
Export Playwright storage_state after you log in to Facebook locally (headed browser).

Usage — you must run this from your FastPost project folder (not C:\\Windows\\System32).

  cd path\\to\\fast-post-socialv3
  python scripts/export_facebook_storage.py
  python scripts/export_facebook_storage.py C:\\Temp\\my_fb_state.json

Windows (PowerShell): launcher cds to the repo for you:

  .\\scripts\\export_facebook_storage.ps1

Or full path:

  & \"C:\\Users\\YOU\\Desktop\\fast-post-socialv3\\scripts\\export_facebook_storage.ps1\"

Then in the FastPost dashboard: Accounts → Session JSON → paste the ENTIRE file contents → Save.

The JSON is sensitive (session cookies). Do not commit it or share it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "facebook_storage.json").resolve()
    print("Opening Chromium. Log in to Facebook completely, then return here.")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
        await asyncio.to_thread(input, "Press Enter after login is finished… ")
        await context.storage_state(path=str(out))
        await browser.close()
    print(f"Wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
