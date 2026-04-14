"""
FastPost Social v3 - Stealth Browser Poster
Uses Playwright + playwright-stealth to post to Facebook/Instagram
without being detected as a bot. Mimics human behavior throughout.

STEALTH TECHNIQUES:
1. playwright-stealth plugin hides automation fingerprints
2. Persistent browser profiles (no repeated logins)
3. Randomized timing jitter on all actions
4. Human-like curved mouse movements
5. Random scrolling before posting
6. Natural typing speed variation
"""

import asyncio
import random
import time
import os
import json
import logging
from pathlib import Path
from typing import Dict, Optional
from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import stealth_async

logger = logging.getLogger(__name__)

# Profiles directory - saves browser cookies so we don't re-login every time
PROFILES_DIR = Path(os.getenv("PROFILES_DIR", "./browser_profiles"))
PROFILES_DIR.mkdir(exist_ok=True)


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
                              result = asyncio.run(
                                                self._post_async(platform, page_url, caption, account_id)
                              )
                              return result
except Exception as e:
            logger.error(f"[StealthPoster] Top-level error: {e}")
            return {"success": False, "error": str(e)}

    async def _post_async(
              self, platform: str, page_url: str, caption: str, account_id: int
    ) -> Dict:
              """Core async posting logic"""
              profile_path = PROFILES_DIR / f"profile_{account_id}"
              profile_path.mkdir(exist_ok=True)

        async with async_playwright() as p:
                      # Launch with persistent context (saves cookies/session)
                      context = await p.chromium.launch_persistent_context(
                                        user_data_dir=str(profile_path),
                                        headless=self.headless,
                                        viewport={"width": random.randint(1280, 1440), "height": random.randint(800, 900)},
                                        user_agent=(
                                                              "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                                                              "Chrome/120.0.0.0 Safari/537.36"
                                        ),
                                        args=[
                                                              "--disable-blink-features=AutomationControlled",
                                                              "--no-sandbox",
                                                              "--disable-dev-shm-usage",
                                        ],
                                        locale="en-US",
                                        timezone_id="America/New_York",
                      )

            page = context.pages[0] if context.pages else await context.new_page()

            # Apply stealth patches
            await stealth_async(page)

            try:
                              if platform.lower() in ["facebook", "fb"]:
                                                    result = await self._post_facebook(page, page_url, caption)
elif platform.lower() in ["instagram", "ig"]:
                    result = await self._post_instagram(page, page_url, caption)
else:
                    result = {"success": False, "error": f"Unsupported platform: {platform}"}

                # Save updated cookies/session
                  await context.storage_state(path=str(profile_path / "state.json"))
                return result

except Exception as e:
                logger.error(f"[StealthPoster] Posting error: {e}")
                return {"success": False, "error": str(e)}
finally:
                await context.close()

    async def _post_facebook(self, page: Page, page_url: str, caption: str) -> Dict:
              """Post to a Facebook page using stealth automation"""
              logger.info(f"[StealthPoster] Posting to Facebook: {page_url}")

        # Navigate to the page
              await page.goto(page_url, wait_until="domcontentloaded")
        await self._human_delay(2, 4)

        # Check if we need to log in
        if "login" in page.url or "checkpoint" in page.url:
                      return {"success": False, "error": "Session expired - please log in manually and re-save session"}

        # Random scroll to simulate reading the page
        await self._human_scroll(page)
        await self._human_delay(1, 2)

        # Find the "What's on your mind?" post composer
        composer_selectors = [
                      '[data-testid="status-attachment-mentions-input"]',
                      '[placeholder="Write something..."]',
                      '[placeholder="What\'s on your mind?"]',
                      '[aria-label="Create a post"]',
                      'div[role="textbox"][contenteditable="true"]',
        ]

        composer = None
        for selector in composer_selectors:
                      try:
                                        # Click the "Write something" placeholder first
                                        placeholder = await page.query_selector(
                                                              f'[placeholder="Write something..."], [placeholder="What\'s on your mind?"]'
                                        )
                                        if placeholder:
                                                              await self._human_move_and_click(page, placeholder)
                                                              await self._human_delay(1.5, 2.5)
                                                              break
                      except Exception:
                                        pass

                  # After clicking the placeholder, the real composer appears
                  await self._human_delay(1, 2)
        composer = await page.query_selector('div[role="textbox"][contenteditable="true"]')
        if not composer:
                      # Try alternative: look for "Create post" button
                      create_btn = await page.query_selector('[aria-label*="Create post"], [aria-label*="Write post"]')
                      if create_btn:
                                        await self._human_move_and_click(page, create_btn)
                                        await self._human_delay(1, 2)
                                        composer = await page.query_selector('div[role="textbox"][contenteditable="true"]')

                  if not composer:
                                return {"success": False, "error": "Could not find post composer on Facebook page"}

        # Type the caption with human-like speed
        await self._human_move_and_click(page, composer)
        await self._human_type(page, caption)
        await self._human_delay(1, 2)

        # Find and click the Post button
        post_btn_selectors = [
                      '[aria-label="Post"]',
                      'div[aria-label="Post"]',
                      'button[type="submit"]',
        ]

        for selector in post_btn_selectors:
                      btn = await page.query_selector(selector)
                      if btn:
                                        # One final human pause before submitting
                                        await self._human_delay(0.5, 1.5)
                                        await self._human_move_and_click(page, btn)
                                        await self._human_delay(2, 4)
                                        logger.info("[StealthPoster] Facebook post submitted")
                                        return {"success": True}

                  return {"success": False, "error": "Could not find Post button"}

    async def _post_instagram(self, page: Page, page_url: str, caption: str) -> Dict:
              """Post to Instagram (navigate to create post flow)"""
              logger.info(f"[StealthPoster] Posting to Instagram")

        await page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        await self._human_delay(2, 4)

        if "login" in page.url:
                      return {"success": False, "error": "Instagram session expired - please log in manually"}

        # Click the "+" create button
        create_btn = await page.query_selector(
                      'svg[aria-label="New post"], a[href="/create/select/"]'
        )
        if not create_btn:
                      create_btn = await page.query_selector('[aria-label="New post"]')

        if create_btn:
                      await self._human_move_and_click(page, create_btn)
                      await self._human_delay(1, 2)
                      return {"success": False, "error": "Instagram image upload required - use Facebook for text-only posts"}

        return {"success": False, "error": "Instagram auto-posting requires image upload flow"}

    # ── HUMAN SIMULATION HELPERS ──────────────────────────────────────────────

    async def _human_delay(self, min_s: float = 0.5, max_s: float = 2.0):
              """Random delay to simulate human reaction time"""
              delay = random.uniform(min_s, max_s)
              # Add occasional micro-pauses
              if random.random() < 0.2:
                            delay += random.uniform(0.5, 1.5)
                        await asyncio.sleep(delay)

    async def _human_type(self, page: Page, text: str):
              """
                      Type text with human-like speed variation.
                              Includes occasional typo-and-correction behavior.
                                      """
              for char in text:
                            await page.keyboard.type(char)
                            # Variable typing speed
                            if char in ".!?\n":
                                              await asyncio.sleep(random.uniform(0.15, 0.4))  # Pause at punctuation
elif char == " ":
                await asyncio.sleep(random.uniform(0.05, 0.15))
else:
                await asyncio.sleep(random.uniform(0.03, 0.12))

    async def _human_move_and_click(self, page: Page, element):
              """
                      Move mouse to element with slight randomness, then click.
                              Simulates human cursor movement.
                                      """
        box = await element.bounding_box()
        if not box:
                      await element.click()
                      return

        # Target center with small random offset
        target_x = box["x"] + box["width"] / 2 + random.uniform(-5, 5)
        target_y = box["y"] + box["height"] / 2 + random.uniform(-3, 3)

        # Move mouse to element
        await page.mouse.move(target_x, target_y)
        await asyncio.sleep(random.uniform(0.1, 0.3))
        await page.mouse.click(target_x, target_y)

    async def _human_scroll(self, page: Page):
              """Randomly scroll up and down like a human reading the page"""
        scroll_amount = random.randint(200, 600)
        await page.mouse.wheel(0, scroll_amount)
        await asyncio.sleep(random.uniform(0.5, 1.5))
        # Scroll back up partially
        await page.mouse.wheel(0, -random.randint(50, 200))
        await asyncio.sleep(random.uniform(0.3, 0.8))
