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
import sys
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
PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def _running_in_paas() -> bool:
    """Render, GitHub Actions, Fly, Railway, K8s, etc. — never use a local X server here."""
    return bool(
        os.getenv("RENDER")
        or os.getenv("CI")
        or os.getenv("KUBERNETES_SERVICE_HOST")
        or os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("RAILWAY_PUBLIC_DOMAIN")
        or os.getenv("FLY_APP_NAME")
    )


def _default_headless() -> bool:
    """
    Headless is required on servers without a real X11/Wayland session. Some hosts set a
    bogus DISPLAY; Chromium then picks ozone/x11 and exits. PaaS is always headless.
    """
    if _running_in_paas():
        logger.info("[StealthPoster] PaaS environment — Chromium headless=True")
        return True
    headed = os.getenv("FB_HEADED", "").lower() in ("1", "true", "yes")
    if sys.platform == "win32":
        return not headed
    display = (os.getenv("DISPLAY") or "").strip()
    if headed and display:
        return False
    if headed and not display:
        logger.warning(
            "[StealthPoster] FB_HEADED=1 but DISPLAY is unset — using headless (typical for Docker)"
        )
    return True


def _browser_subprocess_env(headless: bool) -> Dict[str, str]:
    """Drop X11/Wayland hints so Chromium does not try a headed Ozone path on CI."""
    env = dict(os.environ)
    if headless:
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"):
            env.pop(key, None)
    return env


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
    def __init__(self, db, headless: Optional[bool] = None):
        self.db = db
        if headless is None:
            headless = _default_headless()
        self.headless = headless
        logger.info(
            "[StealthPoster] Chromium headless=%s (Windows desktop: FB_HEADED=1; PaaS: always headless)",
            headless,
        )

    @staticmethod
    def _env_leave_browser_open() -> bool:
        """If True, do not close Chromium when login fails so the user can finish signing in."""
        return os.getenv("FB_LEAVE_BROWSER_OPEN", "1").lower() in ("1", "true", "yes")

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

        result: Dict = {"success": False, "error": "Interrupted"}
        leave_browser_open = False
        playwright = await async_playwright().start()
        context = None
        try:
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
            if self.headless:
                # New headless + Ozone headless avoid x11 when DISPLAY is set but unusable (common on PaaS).
                launch_args.append("--headless=new")
                if sys.platform != "win32":
                    launch_args.append("--ozone-platform=headless")
            context = await playwright.chromium.launch_persistent_context(
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
                args=launch_args,
                locale="en-US",
                timezone_id=os.getenv("FB_TZ", "America/New_York"),
                env=_browser_subprocess_env(self.headless),
            )

            page = context.pages[0] if context.pages else await context.new_page()
            await _stealth.apply_stealth_async(page)

            if platform.lower() in ["facebook", "fb", "both"]:
                result = await self._post_facebook(page, page_url, caption)
            elif platform.lower() in ["instagram", "ig"]:
                result = await self._post_instagram(page, page_url, caption)
            else:
                result = {"success": False, "error": f"Unsupported platform: {platform}"}

            try:
                await context.storage_state(path=str(state_file))
            except Exception as ex:
                logger.warning("[StealthPoster] storage_state backup skipped: %s", ex)
        except Exception as e:
            logger.error("[StealthPoster] Posting error: %s", e)
            result = {"success": False, "error": str(e)}
        finally:
            leave_browser_open = bool(
                isinstance(result, dict) and result.get("leave_browser_open")
            )
            if leave_browser_open:
                logger.info(
                    "[StealthPoster] Leaving Chromium open so you can finish logging in. "
                    "When done, close the window or run Post again."
                )
            elif context is not None:
                await context.close()
            if not leave_browser_open:
                await playwright.stop()

        out = dict(result) if isinstance(result, dict) else {"success": False, "error": str(result)}
        out.pop("leave_browser_open", None)
        return out

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
            if await self._facebook_needs_login(page) or not await self._facebook_feed_looks_loaded(page):
                logger.info(
                    "[StealthPoster] No composer yet — waiting again for login / page to finish loading."
                )
                topup = float(os.getenv("FB_LOGIN_TOPUP_SECONDS", "480"))
                again = await self._wait_for_facebook_session(page, target, max_wait_s=topup)
                if again is not None:
                    return again
                await self._human_scroll(page)
                composer = await self._resolve_facebook_composer(page)

        if not composer:
            if await self._facebook_needs_login(page):
                err = {
                    "success": False,
                    "error": (
                        "Facebook still shows login or a security step. Finish in the Chromium window; "
                        "it will stay open so you are not rushed. Then click Post again."
                    ),
                }
                if self._env_leave_browser_open():
                    err["leave_browser_open"] = True
                return err
            err = {
                "success": False,
                "error": (
                    "Could not find the post composer. Log in to Facebook in the browser window first. "
                    "If your saved URL is a /people/... profile link, use your Page username URL from "
                    "Meta (e.g. facebook.com/YourPageName) for posting after you're logged in."
                ),
            }
            if self._env_leave_browser_open():
                err["leave_browser_open"] = True
            return err

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

    async def _wait_for_facebook_session(
        self, page: Page, target_url: str, *, max_wait_s: Optional[float] = None
    ) -> Optional[Dict]:
        """
        Wait for Meta login/checkpoint to finish. Facebook often paints the login form late, so we require
        a minimum settle time and several consecutive "not login" checks before continuing.
        """
        max_wait = float(max_wait_s) if max_wait_s is not None else float(
            os.getenv("FB_LOGIN_WAIT_SECONDS", "900")
        )
        min_settle = float(os.getenv("FB_PAGE_SETTLE_SECONDS", "15"))
        stable_need = int(os.getenv("FB_LOGIN_STABLE_POLLS", "4"))
        poll_s = 2.5
        start = time.monotonic()
        logged_waiting = False
        stable = 0

        while time.monotonic() - start < max_wait:
            elapsed = time.monotonic() - start
            needs = await self._facebook_needs_login(page)

            if needs:
                stable = 0
                if not logged_waiting:
                    logger.info(
                        "[StealthPoster] Login or checkpoint detected — sign in in Chromium; "
                        "waiting up to %.0fs (page is not closed while you log in).",
                        max_wait,
                    )
                    logged_waiting = True
                await asyncio.sleep(poll_s)
                continue

            # Not login — but FB often shows a blank shell first; do not navigate away too early.
            if elapsed < min_settle:
                await asyncio.sleep(poll_s)
                continue

            stable += 1
            if stable < stable_need:
                await asyncio.sleep(poll_s)
                continue

            try:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=90000)
                await asyncio.sleep(2.5)
            except Exception as e:
                logger.warning("[StealthPoster] Navigation after session: %s", e)

            if await self._facebook_needs_login(page):
                stable = 0
                await asyncio.sleep(poll_s)
                continue

            if logged_waiting:
                logger.info("[StealthPoster] Session looks ready — continuing.")
            return None

        err: Dict = {
            "success": False,
            "error": (
                f"Facebook login did not finish within {int(max_wait)} seconds. "
                "The browser will stay open so you can keep going; when finished, click Post again."
            ),
        }
        if self._env_leave_browser_open():
            err["leave_browser_open"] = True
        return err

    async def _facebook_feed_looks_loaded(self, page: Page) -> bool:
        """Heuristic: enough DOM to try composer (avoids exiting wait while still a white screen)."""
        try:
            html = await page.content()
            if len(html) < 8000:
                return False
            # Login walls are usually smaller or have password inputs (handled elsewhere)
            return True
        except Exception:
            return False

    async def _facebook_needs_login(self, page: Page) -> bool:
        """True if Meta is asking us to log in / verify — including modal login on /people/ URLs."""
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

            # ── Modal / overlay login (e.g. "See more from …" on public /people/… profiles) ──
            try:
                dialog_login = page.locator(
                    '[role="dialog"] input[type="password"], '
                    '[aria-modal="true"] input[type="password"]'
                )
                dc = await dialog_login.count()
                if dc > 0:
                    for i in range(min(dc, 4)):
                        try:
                            if await dialog_login.nth(i).is_visible():
                                return True
                        except Exception:
                            continue
            except Exception:
                pass

            try:
                if await page.get_by_text(re.compile(r"see more from", re.I)).count() > 0:
                    if await page.locator('input[type="password"]').count() > 0:
                        return True
            except Exception:
                pass

            # Logged-out chrome: "Email or phone" placeholder + password (top bar or modal)
            try:
                ph_email = page.locator(
                    '[placeholder*="Email or phone"], [placeholder*="mail"], '
                    '[aria-label*="Email or phone"], [aria-label*="phone"]'
                )
                pwd_all = page.locator('input[type="password"]')
                pc = await pwd_all.count()
                if await ph_email.count() > 0 and pc > 0:
                    for i in range(min(pc, 6)):
                        try:
                            if await pwd_all.nth(i).is_visible():
                                return True
                        except Exception:
                            continue
            except Exception:
                pass

            # Public profile path: no composer until logged in; visible password means gate
            if "/people/" in path and "facebook.com" in host:
                try:
                    pwds = page.locator('input[type="password"]')
                    for i in range(min(await pwds.count(), 8)):
                        try:
                            if await pwds.nth(i).is_visible():
                                return True
                        except Exception:
                            continue
                except Exception:
                    pass

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
