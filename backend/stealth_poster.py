"""
FastPost Social v3 - Stealth Browser Poster
Uses Playwright + playwright-stealth to post to Facebook/Instagram
without being detected as a bot. Mimics human behavior throughout.

STEALTH TECHNIQUES:
1. playwright-stealth plugin hides automation fingerprints
2. Persistent browser profiles (no repeated logins — same profile per account_id)
3. Randomized timing jitter on all actions
4. Human-like curved mouse movements
5. Random scrolling before posting
6. Natural typing speed variation
"""

import asyncio
import random
import os
import re
import time
import logging
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, Optional, List, Union
from playwright.async_api import async_playwright, Page, Locator, ElementHandle
from playwright_stealth import Stealth

logger = logging.getLogger(__name__)

_stealth = Stealth()

PROFILES_DIR = Path(os.getenv("PROFILES_DIR", "./browser_profiles"))
PROFILES_DIR.mkdir(exist_ok=True)


def _normalize_facebook_page_url(url: str) -> str:
    """Use desktop www.facebook.com so composer selectors match."""
    u = (url or "").strip()
    if not u.startswith("http"):
        u = "https://" + u
    u = u.replace("://m.facebook.com", "://www.facebook.com")
    u = u.replace("://facebook.com", "://www.facebook.com")
    if "://www.facebook.com" not in u and "facebook.com" in u:
        u = re.sub(r"https?://(?!www\.)", "https://www.", u, count=1)
    return u.rstrip("/") + "/"


class StealthPoster:
    def __init__(self, db, headless: bool = False):
        self.db = db
        self.headless = headless

    def post(self, platform: str, page_url: str, caption: str, account_id: int) -> Dict:
        """
        Synchronous wrapper. Runs the async post in an event loop.
        Returns {"success": True} or {"success": False, "error": "..."}
        """
        try:
            result = asyncio.run(self._post_async(platform, page_url, caption, account_id))
            return result
        except Exception as e:
            logger.error(f"[StealthPoster] Top-level error: {e}")
            return {"success": False, "error": str(e)}

    async def _post_async(
        self, platform: str, page_url: str, caption: str, account_id: int
    ) -> Dict:
        """Core async posting logic"""
        profile_path = (PROFILES_DIR / f"profile_{account_id}").resolve()
        profile_path.mkdir(parents=True, exist_ok=True)
        state_file = profile_path / "state.json"

        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                headless=self.headless,
                viewport={
                    "width": random.randint(1280, 1440),
                    "height": random.randint(800, 900),
                },
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
                locale="en-US",
                timezone_id=os.getenv("FB_TZ", "America/New_York"),
            )

            page = context.pages[0] if context.pages else await context.new_page()
            await _stealth.apply_stealth_async(page)

            try:
                if platform.lower() in ["facebook", "fb", "both"]:
                    result = await self._post_facebook(page, page_url, caption)
                elif platform.lower() in ["instagram", "ig"]:
                    result = await self._post_instagram(page, page_url, caption)
                else:
                    result = {"success": False, "error": f"Unsupported platform: {platform}"}

                try:
                    await context.storage_state(path=str(state_file))
                except Exception as ex:
                    logger.warning(f"[StealthPoster] storage_state backup skipped: {ex}")
                return result

            except Exception as e:
                logger.error(f"[StealthPoster] Posting error: {e}")
                return {"success": False, "error": str(e)}
            finally:
                await context.close()

    async def _post_facebook(self, page: Page, page_url: str, caption: str) -> Dict:
        """Post to a Facebook Page or profile (desktop web)."""
        target = _normalize_facebook_page_url(page_url)
        logger.info(f"[StealthPoster] Posting to Facebook: {target}")

        await page.goto(target, wait_until="domcontentloaded", timeout=90000)
        await self._human_delay(2, 4)

        # Wait while login / checkpoint is showing — do NOT look for composer until session is ready
        login_wait = await self._wait_for_facebook_session(page, target)
        if login_wait is not None:
            return login_wait

        await self._human_scroll(page)
        await self._human_delay(1, 2)

        composer = await self._resolve_facebook_composer(page)
        if not composer:
            if await self._facebook_needs_login(page):
                return {
                    "success": False,
                    "error": (
                        "Facebook is still showing a login or security screen. Finish signing in in the "
                        "browser window, wait until you see your Page or feed, then click Post again."
                    ),
                }
            return {
                "success": False,
                "error": (
                    "Could not find the post composer. Use your Page's Facebook URL "
                    "(https://www.facebook.com/YourPageName), and make sure you're posting as a user "
                    "who can manage that Page."
                ),
            }

        await self._human_move_and_click(page, composer)
        await self._human_delay(0.4, 0.9)
        await composer.click()
        await self._human_delay(0.3, 0.6)
        await self._human_type(page, caption)
        await self._human_delay(1, 2)

        posted = await self._click_facebook_submit(page)
        if posted:
            logger.info("[StealthPoster] Facebook post submitted")
            return {"success": True}

        return {"success": False, "error": "Could not find or click the Post button (check for dialogs or blocking)."}

    async def _wait_for_facebook_session(self, page: Page, target_url: str) -> Optional[Dict]:
        """
        If Meta shows login/checkpoint, wait until the user completes it (same browser window).
        Then navigate to the Page URL again. Returns an error dict only on timeout.
        """
        max_wait_s = float(os.getenv("FB_LOGIN_WAIT_SECONDS", "600"))
        poll_s = 2.5
        start = time.monotonic()
        logged_waiting = False

        while time.monotonic() - start < max_wait_s:
            if await self._facebook_needs_login(page):
                if not logged_waiting:
                    logger.info(
                        "[StealthPoster] Login or checkpoint detected — sign in in the Chromium window; "
                        "waiting up to %.0fs before looking for the composer.",
                        max_wait_s,
                    )
                    logged_waiting = True
                await asyncio.sleep(poll_s)
                continue

            # Session looks usable — open the target Page (user may have landed on home after login)
            try:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=90000)
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning("[StealthPoster] Navigation after login: %s", e)

            # Re-check in case a second redirect sent us back to login
            if await self._facebook_needs_login(page):
                await asyncio.sleep(poll_s)
                continue

            if logged_waiting:
                logger.info("[StealthPoster] Session ready — continuing to composer.")
            return None

        return {
            "success": False,
            "error": (
                f"Facebook login did not finish within {int(max_wait_s)} seconds. "
                "Complete email/password and any security checks in the Chromium window, then click Post again."
            ),
        }

    async def _facebook_needs_login(self, page: Page) -> bool:
        """True if we're on a login / checkpoint / recovery flow (not ready to use composer)."""
        try:
            u = page.url.lower()
            parsed = urlparse(page.url)
            host = (parsed.netloc or "").lower()
            path = (parsed.path or "").lower()

            if host.startswith("login.") or "login.facebook.com" in host:
                return True

            if "/login" in path or path.endswith("/login"):
                return True
            for marker in (
                "checkpoint",
                "two_factor",
                "two_step",
                "recover",
                "device_id",
                "device_based",
            ):
                if marker in path or marker in u:
                    return True

            if "checkpoint" in u or "two_step_verification" in u:
                return True
            if "facebook.com/login" in u or "/login.php" in u:
                return True

            # Main login form: email + password visible (many Meta layouts)
            try:
                pwd = page.locator('input[type="password"]').first
                mail = page.locator(
                    'input[name="email"], input#email, input[name="email_address"], '
                    'input[type="email"], input[type="tel"], input[name="phone"]'
                ).first
                if await pwd.count() > 0 and await pwd.is_visible():
                    if await mail.count() > 0 and await mail.is_visible():
                        return True
            except Exception:
                pass

            # Named login form
            try:
                if await page.locator("#login_form, form#login_form, [data-testid='royal_login_form']").count() > 0:
                    if await page.locator('input[type="password"]').count() > 0:
                        return True
            except Exception:
                pass

        except Exception:
            # Don't treat detection errors as "must log in" (would block forever)
            return False

        return False

    async def _resolve_facebook_composer(self, page: Page) -> Optional[Locator]:
        """Find composer on feed or inside a Create-post dialog (Meta changes UI often)."""
        # Already-visible composers (Page feed, profile)
        direct_selectors: List[str] = [
            '[data-testid="status-attachment-mentions-input"]',
            'div[data-lexical-editor="true"]',
            'div[role="textbox"][contenteditable="true"]',
            'div[contenteditable="true"][role="textbox"]',
            '[aria-label*="What\'s on your mind"]',
            '[aria-label*="Create a public post"]',
            '[aria-label*="Write something"]',
            '[placeholder="Write something..."]',
            '[placeholder*="Write something"]',
            '[placeholder*="Create a post"]',
        ]

        composer = await self._first_visible_locator(page, direct_selectors, root=None)
        if composer:
            return composer

        # Open "Create post" / composer entry — many variants (Meta changes labels often)
        opener_selectors: List[str] = [
            '[data-testid="status-attachment-mentions-input"]',
            '[aria-label*="Create post"]',
            '[aria-label*="Create a post"]',
            '[aria-label*="Write something"]',
            'div[role="button"][aria-label*="Create post"]',
            'div[role="button"][aria-label*="Write"]',
            "span:has-text(\"What's on your mind\")",
            'span:has-text("Write something")',
        ]

        for sel in opener_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.click(timeout=5000)
                    await self._human_delay(1.2, 2.0)
                    break
            except Exception:
                continue

        # Try role-based (English UI)
        for name in (
            "Create post",
            "Create a post",
            "What's on your mind?",
            "Write something",
        ):
            try:
                btn = page.get_by_role("button", name=name).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click(timeout=5000)
                    await self._human_delay(1.2, 2.0)
                    break
            except Exception:
                continue

        # Composer inside dialog / modal
        dialog_roots = [
            page.locator('[role="dialog"]').first,
            page.locator('[aria-modal="true"]').first,
        ]
        for root_loc in dialog_roots:
            try:
                if await root_loc.count() == 0:
                    continue
                if not await root_loc.is_visible():
                    continue
                composer = await self._first_visible_locator(page, direct_selectors, root=root_loc)
                if composer:
                    return composer
            except Exception:
                continue

        # Again on full page after click
        composer = await self._first_visible_locator(page, direct_selectors, root=None)
        if composer:
            return composer

        # Poll briefly for lazy-loaded composer
        for _ in range(24):
            composer = await self._first_visible_locator(page, direct_selectors, root=None)
            if composer:
                return composer
            dialog = page.locator('[role="dialog"]').first
            if await dialog.count() > 0 and await dialog.is_visible():
                composer = await self._first_visible_locator(page, direct_selectors, root=dialog)
                if composer:
                    return composer
            await asyncio.sleep(0.5)

        return None

    async def _first_visible_locator(
        self, page: Page, selectors: List[str], root: Optional[Locator]
    ) -> Optional[Locator]:
        scope = root if root is not None else page.locator("body")
        for sel in selectors:
            try:
                loc = scope.locator(sel).first
                if await loc.count() > 0:
                    try:
                        await loc.wait_for(state="visible", timeout=2500)
                    except Exception:
                        if not await loc.is_visible():
                            continue
                    return loc
            except Exception:
                continue
        return None

    async def _click_facebook_submit(self, page: Page) -> bool:
        """Click Post / Publish in main view or active dialog."""
        scopes: List[Locator] = [page.locator('[role="dialog"]').first, page.locator("body")]

        for scope in scopes:
            try:
                if await scope.count() == 0:
                    continue
                for name in ("Post", "Publish", "Share", "Next"):
                    try:
                        btn = scope.get_by_role("button", name=name).first
                        if await btn.count() > 0 and await btn.is_enabled():
                            await self._human_delay(0.4, 1.0)
                            await btn.click(timeout=8000)
                            await self._human_delay(2, 3)
                            return True
                    except Exception:
                        continue

                for sel in (
                    '[aria-label="Post"]',
                    '[aria-label="Publish"]',
                    'div[role="button"][aria-label="Post"]',
                    'div[aria-label="Post"]',
                ):
                    try:
                        b = scope.locator(sel).first
                        if await b.count() > 0 and await b.is_visible():
                            await self._human_delay(0.4, 1.0)
                            await b.click(timeout=8000)
                            await self._human_delay(2, 3)
                            return True
                    except Exception:
                        continue
                try:
                    pb = scope.get_by_role("button", name=re.compile(r"^(post|publish)$", re.I)).first
                    if await pb.count() > 0 and await pb.is_visible():
                        await self._human_delay(0.4, 1.0)
                        await pb.click(timeout=8000)
                        await self._human_delay(2, 3)
                        return True
                except Exception:
                    pass
            except Exception:
                continue
        return False

    async def _post_instagram(self, page: Page, page_url: str, caption: str) -> Dict:
        """Post to Instagram (navigate to create post flow)"""
        logger.info("[StealthPoster] Posting to Instagram")

        await page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        await self._human_delay(2, 4)

        if "login" in page.url:
            return {"success": False, "error": "Instagram session expired - please log in manually"}

        create_btn = await page.query_selector(
            'svg[aria-label="New post"], a[href="/create/select/"]'
        )
        if not create_btn:
            create_btn = await page.query_selector('[aria-label="New post"]')

        if create_btn:
            await self._human_move_and_click(page, create_btn)
            await self._human_delay(1, 2)
            return {
                "success": False,
                "error": "Instagram image upload required - use Facebook for text-only posts",
            }

        return {"success": False, "error": "Instagram auto-posting requires image upload flow"}

    async def _human_delay(self, min_s: float = 0.5, max_s: float = 2.0):
        """Random delay to simulate human reaction time"""
        delay = random.uniform(min_s, max_s)
        if random.random() < 0.2:
            delay += random.uniform(0.5, 1.5)
        await asyncio.sleep(delay)

    async def _human_type(self, page: Page, text: str):
        """Type text with human-like speed variation."""
        for char in text:
            await page.keyboard.type(char)
            if char in ".!?\n":
                await asyncio.sleep(random.uniform(0.15, 0.4))
            elif char == " ":
                await asyncio.sleep(random.uniform(0.05, 0.15))
            else:
                await asyncio.sleep(random.uniform(0.03, 0.12))

    async def _human_move_and_click(self, page: Page, element: Union[Locator, ElementHandle]):
        """Move mouse to element with slight randomness, then click."""
        try:
            box = await element.bounding_box()
        except Exception:
            box = None
        if not box:
            await element.click()
            return

        target_x = box["x"] + box["width"] / 2 + random.uniform(-5, 5)
        target_y = box["y"] + box["height"] / 2 + random.uniform(-3, 3)

        await page.mouse.move(target_x, target_y)
        await asyncio.sleep(random.uniform(0.1, 0.3))
        await page.mouse.click(target_x, target_y)
