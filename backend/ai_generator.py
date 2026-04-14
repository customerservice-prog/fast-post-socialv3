"""
FastPost Social v3 - Local Content Generator
Builds 3 daily post drafts from your crawl data — no external AI APIs.
Post types: Morning Promo, Mid-day Tip, Evening Social Proof
"""

import re
from datetime import date
from typing import List, Dict, Optional, Tuple


class AIContentGenerator:
    """Generates captions from structured crawl data using templates and light text shaping."""

    def __init__(self, api_key: Optional[str] = None):
        # api_key accepted for backward compatibility; ignored (no cloud APIs).
        self.schedule = {
            "morning": "09:00",
            "afternoon": "13:00",
            "evening": "18:00",
        }

    def generate_daily_posts(
        self,
        business_name: str,
        business_url: str,
        platform: str,
        crawl_data: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Generate 3 posts for today: morning promo, afternoon tip, evening social proof.
        Returns list of post dicts ready to insert into DB.
        """
        today = date.today().strftime("%Y-%m-%d")
        ctx = self._extract_context(business_name, business_url, crawl_data)
        seed = date.today().toordinal() + len(business_name)

        posts_meta = [
            ("morning_promo", self.schedule["morning"], self._morning_promo),
            ("afternoon_tip", self.schedule["afternoon"], self._afternoon_tip),
            ("evening_proof", self.schedule["evening"], self._evening_proof),
        ]

        posts = []
        for post_type, time_str, builder in posts_meta:
            caption, image_prompt = builder(ctx, platform, seed)
            seed += 17
            posts.append(
                {
                    "type": post_type,
                    "caption": caption,
                    "image_prompt": image_prompt,
                    "scheduled_time": f"{today} {time_str}:00",
                }
            )

        return posts

    def _extract_context(
        self, business_name: str, business_url: str, crawl_data: Optional[Dict]
    ) -> Dict:
        cd = crawl_data or {}
        services = [s for s in cd.get("services") or [] if s]
        prices = [p for p in cd.get("prices") or [] if p]
        headings = [h for h in cd.get("key_headings") or [] if h]
        images = [i for i in cd.get("image_descriptions") or [] if i]
        samples = [t for t in cd.get("text_samples") or [] if t]
        summary = (cd.get("summary") or "").strip()
        snippet = ""
        if samples:
            snippet = self._clean_snippet(samples[0], 220)
        elif summary:
            snippet = self._clean_snippet(summary, 220)
        return {
            "business_name": business_name.strip() or "Our business",
            "business_url": business_url.strip(),
            "services": services[:12],
            "prices": prices[:5],
            "headings": headings[:8],
            "images": images[:6],
            "snippet": snippet,
        }

    def _clean_snippet(self, text: str, max_len: int) -> str:
        t = re.sub(r"\s+", " ", text).strip()
        if len(t) <= max_len:
            return t
        return t[: max_len - 1].rsplit(" ", 1)[0] + "…"

    def _pick(self, options: List[str], seed: int) -> str:
        if not options:
            return ""
        return options[seed % len(options)]

    def _service_line(self, ctx: Dict, seed: int) -> str:
        svcs = ctx["services"]
        if len(svcs) >= 2:
            a, b = self._pick(svcs, seed), self._pick(svcs, seed + 3)
            if a != b:
                return f"From {a} to {b}, we help you get it right."
        if svcs:
            return f"Ask us about {svcs[0]} — we'd love to help."
        if ctx["headings"]:
            return f"Focused on: {ctx['headings'][0]}."
        return "Reach out and we'll walk you through options."

    def _morning_promo(self, ctx: Dict, platform: str, seed: int) -> Tuple[str, str]:
        name = ctx["business_name"]
        opens = [
            f"Good morning! ☀️ {name} is here when you're ready to plan your next step.",
            f"Rise and shine! 🌤️ Start the day with {name} — local, friendly, and ready to help.",
            f"Morning crew! 👋 {name} has openings and answers — let's make today easier.",
        ]
        body = self._service_line(ctx, seed)
        price_hint = ""
        if ctx["prices"]:
            price_hint = f" See current offers from {ctx['prices'][0]}."
        ctas = [
            "Message us to reserve your spot.",
            "Call or DM — we'll get you scheduled.",
            "Tap in — we reply fast.",
        ]
        caption = f"{self._pick(opens, seed)}\n\n{body}{price_hint}\n\n{self._pick(ctas, seed + 1)}"
        caption = self._append_platform_tail(caption, ctx, platform, seed)
        img = self._image_prompt(name, ctx, "bright, welcoming, professional local business scene")
        return caption, img

    def _afternoon_tip(self, ctx: Dict, platform: str, seed: int) -> Tuple[str, str]:
        name = ctx["business_name"]
        tips = [
            (
                "Quick tip: book early for the best selection and calmer planning.",
                "Early planning saves stress and money.",
            ),
            (
                "Quick tip: ask what's included before you compare quotes — apples to apples matters.",
                "Clear questions get clear answers.",
            ),
            (
                "Quick tip: share your headcount and date up front — we can match you faster.",
                "Details help us help you.",
            ),
        ]
        tip_title, tip_sub = tips[seed % len(tips)]
        extra = ""
        if ctx["snippet"]:
            extra = f"\n\nIn our own words: {ctx['snippet']}"
        elif ctx["headings"]:
            extra = f"\n\nPopular topic: {ctx['headings'][0]}."
        caption = (
            f"💡 {tip_title}\n{tip_sub}\n\n"
            f"{name} is happy to guide you.{extra}\n\n"
            f"Questions? Drop a comment or DM."
        )
        caption = self._append_platform_tail(caption, ctx, platform, seed + 2)
        img = self._image_prompt(name, ctx, "helpful how-to vibe, clean and friendly")
        return caption, img

    def _evening_proof(self, ctx: Dict, platform: str, seed: int) -> Tuple[str, str]:
        name = ctx["business_name"]
        stories = [
            (
                "Tonight we're grateful for everyone who trusted us this season.",
                "Your events and projects keep our team motivated.",
            ),
            (
                "Love seeing our neighbors choose local. Thank you for the support.",
                "Every referral and kind word matters.",
            ),
            (
                "Wrapping the day thankful for another round of great customers.",
                "We don't take your trust lightly.",
            ),
        ]
        s1, s2 = stories[seed % len(stories)]
        questions = [
            "What's the next milestone you're planning for?",
            "What would make your next project a home run?",
            "Tell us: what's one thing you'd love to check off your list this month?",
        ]
        caption = (
            f"🌙 {s1}\n{s2}\n\n"
            f"— {name}\n\n"
            f"{self._pick(questions, seed)}"
        )
        caption = self._append_platform_tail(caption, ctx, platform, seed + 4)
        img = self._image_prompt(name, ctx, "warm community evening feel, authentic local business")
        return caption, img

    def _append_platform_tail(self, caption: str, ctx: Dict, platform: str, seed: int) -> str:
        pl = platform.lower()
        if pl in ("instagram", "ig"):
            tags = self._build_hashtags(ctx["business_name"], ctx["services"], seed)
            if tags:
                return f"{caption.rstrip()}\n\n{tags}"
        return caption

    def _build_hashtags(self, business_name: str, services: List[str], seed: int) -> str:
        raw = [business_name] + services
        tags = []
        for r in raw:
            w = re.sub(r"[^a-zA-Z0-9]+", "", r.replace(" ", "").lower())
            if len(w) >= 3 and w not in tags:
                tags.append(f"#{w[:30]}")
            if len(tags) >= 8:
                break
        if not tags:
            tags = ["#localbusiness", "#smallbusiness"]
        return " ".join(tags)

    def _image_prompt(self, business_name: str, ctx: Dict, mood: str) -> str:
        parts = [f"Photo concept for {business_name}: {mood}."]
        if ctx["services"]:
            parts.append(f"Subtle nod to: {', '.join(ctx['services'][:4])}.")
        if ctx["images"]:
            parts.append(f"Visual ideas: {ctx['images'][0][:120]}.")
        return " ".join(parts)
