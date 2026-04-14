#!/usr/bin/env python3
"""
Smoke-test Chromium in real headless mode (Linux CI / Render-style, no X server).
Run from repo root after: pip install -r requirements.txt && playwright install chromium
Exit 0 on success, non-zero on failure.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path


async def _run() -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        print("verify_playwright_headless: install deps first —", e, file=sys.stderr)
        raise SystemExit(2) from e

    td = Path(tempfile.mkdtemp(prefix="fastpost_pw_verify_"))
    try:
        pw = await async_playwright().start()
        try:
            args = [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--headless=new",
            ]
            if sys.platform != "win32":
                args.append("--ozone-platform=headless")
            env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY")}
            ctx = await pw.chromium.launch_persistent_context(
                str(td),
                headless=True,
                args=args,
                env=env,
            )
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.goto("about:blank", timeout=60_000)
            finally:
                await ctx.close()
        finally:
            await pw.stop()
    finally:
        shutil.rmtree(td, ignore_errors=True)

    print("verify_playwright_headless: ok")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
