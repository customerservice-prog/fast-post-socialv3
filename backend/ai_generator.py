"""
FastPost Social v3 - Local Content Generator
Builds 3 daily post drafts from your crawl data — no external AI APIs.
Post types: Morning Promo, Mid-day Tip, Evening Social Proof

Captions are written for longer reads, topical depth, and natural keyword use
(from your crawl) so posts feel specific to the business and support discovery.
"""

import re
from datetime import date
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse

from caption_dedup import max_similarity_vs_recent

# Rotating discovery tags (IG gets more; FB gets a shorter subset). Niche beats generic for trust.
_IG_DISCOVERY_POOL = [
    "smallbusiness", "shoplocal", "supportlocal", "buylocal", "shopsmall",
    "localbusiness", "entrepreneur", "businesstips", "growyourbusiness",
    "marketingtips", "digitalmarketing", "contentstrategy", "communityfirst",
    "supportsmallbusiness", "womeninbusiness", "startup", "businessowner",
    "customerexperience", "valuefirst", "trustlocal", "shopsmallbusiness",
    "locallove", "hustle", "mindset", "behindthescenes", "protips",
    "howto", "explorepage", "instabusiness", "brandstory",
]

_FB_DISCOVERY_POOL = [
    "SmallBusiness", "ShopLocal", "SupportLocal", "LocalBusiness",
    "BusinessTips", "Entrepreneur", "CustomerFirst", "Community",
    "ProTips", "HowTo", "GrowYourBusiness", "ShopSmall",
]


class AIContentGenerator:
    """Generates captions from structured crawl data using templates and light text shaping."""

    def __init__(self) -> None:
        self.schedule = {
            "morning": "09:00",
            "afternoon": "13:00",
            "evening": "18:00",
        }

    def _recent_themes_hint(self, recent_captions: List[str]) -> str:
        """Short text so templates can vary angles vs prior publishes."""
        if not recent_captions:
            return ""
        parts = []
        for t in recent_captions[:12]:
            s = re.sub(r"\s+", " ", (t or "").strip())[:140]
            if s:
                parts.append(s)
        return " · ".join(parts)[:900]

    def _variety_preamble(self, ctx: Dict) -> str:
        h = ctx.get("recent_themes_hint") or ""
        if not h.strip():
            return ""
        return (
            "Fresh angle — steer clear of repeating the same hooks as recent posts on this Page. "
            f"Recent themes included: {h[:420]}{'…' if len(h) > 420 else ''}\n\n"
        )

    def generate_daily_posts(
        self,
        business_name: str,
        business_url: str,
        platform: str,
        crawl_data: Optional[Dict] = None,
        recent_published_captions: Optional[List[str]] = None,
        num_posts: int = 3,
    ) -> List[Dict]:
        """
        Generate up to 3 posts for today: morning promo, afternoon tip, evening social proof.
        num_posts: 1, 2, or 3 — takes the first N slots in that order.
        recent_published_captions: last ~30 published captions for this account (de-duplication).
        """
        today = date.today().strftime("%Y-%m-%d")
        ctx = self._extract_context(business_name, business_url, crawl_data)
        ctx["today_weekday"] = date.today().strftime("%A")
        recent = list(recent_published_captions or [])[:30]
        ctx["recent_themes_hint"] = self._recent_themes_hint(recent)
        seed = date.today().toordinal() + len(business_name)

        posts_meta = [
            ("morning_promo", self.schedule["morning"], self._morning_promo),
            ("afternoon_tip", self.schedule["afternoon"], self._afternoon_tip),
            ("evening_proof", self.schedule["evening"], self._evening_proof),
        ]
        n = max(1, min(3, int(num_posts or 3)))
        posts_meta = posts_meta[:n]

        posts = []
        for post_type, time_str, builder in posts_meta:
            caption, image_prompt = builder(ctx, platform, seed)
            tries = 0
            while tries < 5 and max_similarity_vs_recent(caption, recent) > 0.4:
                tries += 1
                seed += 97
                caption, image_prompt = builder(ctx, platform, seed)
            caption = self._truncate_caption_for_platform(caption, platform)
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
        snippet_a = ""
        snippet_b = ""
        if samples:
            snippet_a = self._clean_snippet(samples[0], 420)
            if len(samples) > 1:
                snippet_b = self._clean_snippet(samples[1], 320)
        elif summary:
            snippet_a = self._clean_snippet(summary, 480)
        return {
            "business_name": business_name.strip() or "Our business",
            "business_url": business_url.strip(),
            "services": services[:12],
            "prices": prices[:5],
            "headings": headings[:8],
            "images": images[:6],
            "snippet_a": snippet_a,
            "snippet_b": snippet_b,
            "url_host_slugs": self._host_slug_tokens(business_url),
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

    def _host_slug_tokens(self, url: str) -> List[str]:
        """Domain stem for branded discovery tags (e.g. friendlyrental from friendlyrental.com)."""
        out: List[str] = []
        try:
            host = (urlparse(url or "").netloc or "").split("@")[-1].split(":")[0].lower()
            host = host.replace("www.", "")
            if "." in host:
                stem = host.split(".")[0]
                if len(stem) >= 4 and stem.isalnum():
                    out.append(stem)
        except Exception:
            pass
        return out[:2]

    def _phrase_to_hashtag_token(self, phrase: str, max_len: int = 28) -> str:
        """Single hashtag body without # (letters/numbers only)."""
        t = re.sub(r"[^a-zA-Z0-9]+", "", (phrase or "").replace(" ", "").lower())
        return t[:max_len] if len(t) >= 3 else ""

    def _unique_hashtag_string(self, bodies: List[str], cap: int) -> str:
        seen = set()
        tags: List[str] = []
        for b in bodies:
            n = re.sub(r"[^a-z0-9]", "", (b or "").lower())
            if len(n) < 3 or n in seen:
                continue
            seen.add(n)
            tags.append(f"#{n[:30]}")
            if len(tags) >= cap:
                break
        return " ".join(tags)

    def _weekday_discovery_tag(self, post_type: str, seed: int) -> str:
        day = date.today().strftime("%A")
        if post_type == "morning_promo":
            return f"{day}Motivation" if seed % 2 == 0 else f"{day}Morning"
        if post_type == "afternoon_tip":
            return f"{day}Tips"
        return f"{day}Thoughts"

    def _build_facebook_hashtags(self, ctx: Dict, seed: int, post_type: str) -> str:
        bodies: List[str] = []
        bodies.append(self._weekday_discovery_tag(post_type, seed))
        bn = self._phrase_to_hashtag_token(ctx["business_name"])
        if bn:
            bodies.append(bn)
        for s in ctx["services"][:6]:
            t = self._phrase_to_hashtag_token(s)
            if t:
                bodies.append(t)
        for h in ctx["headings"][:4]:
            t = self._phrase_to_hashtag_token(h)
            if t:
                bodies.append(t)
        for u in ctx.get("url_host_slugs") or []:
            if u:
                bodies.append(u.lower())
        n_extra = min(6, len(_FB_DISCOVERY_POOL))
        for i in range(n_extra):
            bodies.append(_FB_DISCOVERY_POOL[(seed + i * 3) % len(_FB_DISCOVERY_POOL)])
        return self._unique_hashtag_string(bodies, cap=14)

    def _build_instagram_hashtags(self, ctx: Dict, seed: int, post_type: str) -> str:
        bodies: List[str] = []
        bodies.append(self._weekday_discovery_tag(post_type, seed + 1))
        bn = self._phrase_to_hashtag_token(ctx["business_name"])
        if bn:
            bodies.append(bn)
        for s in ctx["services"][:10]:
            t = self._phrase_to_hashtag_token(s)
            if t:
                bodies.append(t)
        for h in ctx["headings"][:6]:
            t = self._phrase_to_hashtag_token(h)
            if t:
                bodies.append(t)
        for u in ctx.get("url_host_slugs") or []:
            if u:
                bodies.append(u.lower())
        for i in range(24):
            bodies.append(_IG_DISCOVERY_POOL[(seed + i * 7) % len(_IG_DISCOVERY_POOL)])
        return self._unique_hashtag_string(bodies, cap=28)

    def _truncate_caption_for_platform(self, caption: str, platform: str) -> str:
        """Instagram caps at 2200 chars; leave margin when hashtags are appended."""
        pl = platform.lower()
        if pl not in ("instagram", "ig", "both"):
            return caption
        limit = 2180
        if len(caption) <= limit:
            return caption
        cut = caption[: limit - 2]
        if "\n\n" in cut:
            cut = cut.rsplit("\n\n", 1)[0]
        return cut.rstrip() + "…"

    def _topic_phrases(self, ctx: Dict, seed: int, limit: int = 5) -> List[str]:
        """Short topical phrases from crawl (for natural repetition / discovery)."""
        raw = [ctx["business_name"]] + ctx["services"] + ctx["headings"]
        seen = set()
        out = []
        for r in raw:
            t = (r or "").strip()
            if len(t) < 3:
                continue
            key = t.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(t)
            if len(out) >= limit:
                break
        if len(out) < 2:
            out.append("local service")
        return out

    def _weave_topics_paragraph(self, ctx: Dict, seed: int) -> str:
        """One dense paragraph that names real offerings from the crawl."""
        name = ctx["business_name"]
        topics = self._topic_phrases(ctx, seed, 6)
        svcs = ctx["services"]
        heads = ctx["headings"]

        if len(svcs) >= 3:
            a, b, c = self._pick(svcs, seed), self._pick(svcs, seed + 1), self._pick(svcs, seed + 2)
            uniq = []
            for x in (a, b, c):
                if x not in uniq:
                    uniq.append(x)
            if len(uniq) >= 2:
                joined = ", ".join(uniq[:-1]) + f", and {uniq[-1]}"
                return (
                    f"If you're comparing options, {name} focuses on real-world fit—not just price on a page. "
                    f"We regularly help customers thinking through {joined}. "
                    f"Our team talks through timing, what's included, and what to plan for so there are fewer surprises on the day you need everything to go smoothly."
                )

        if topics and len(topics) >= 2:
            t1, t2 = topics[0], topics[min(1, len(topics) - 1)]
            if t1.lower() == name.lower():
                t1 = topics[min(1, len(topics) - 1)]
                t2 = topics[min(2, len(topics) - 1)] if len(topics) > 2 else "your project"
            return (
                f"Whether your priority is {t1.lower()} or {t2.lower()}, {name} is built around clear answers and steady communication. "
                f"We know these decisions are easier when you understand choices upfront—so we explain tradeoffs in plain language and help you pick what matches your timeline and budget."
            )

        if heads:
            h = self._pick(heads, seed)
            return (
                f"A question we hear often is tied to “{h}.” "
                f"At {name}, we use that kind of detail to guide recommendations, because the right setup depends on your goals—not a one-size-fits-all template."
            )

        return (
            f"At {name}, we focus on helpful planning, honest expectations, and follow-through. "
            f"If you're not sure where to start, tell us what you're trying to accomplish and we'll map a simple next step."
        )

    def _bullet_points(self, ctx: Dict, seed: int) -> str:
        lines = []
        for i, s in enumerate(ctx["services"][:3]):
            lines.append(f"• {s}: what to ask about, what's typically included, and how far in advance to book.")
        if not lines and ctx["headings"]:
            for h in ctx["headings"][:3]:
                lines.append(f"• {h} — a practical detail people overlook until the last minute.")
        if not lines:
            lines = [
                "• How to compare quotes fairly (apples-to-apples).",
                "• What to confirm before you commit (delivery, pickup, timing).",
                "• A realistic timeline so you're not rushing the week of your event or project.",
            ]
        return "\n".join(lines[:3])

    def _facebook_semantic_footer(self, ctx: Dict, platform: str, seed: int) -> str:
        """Short, readable line with topical words — not hashtag spam."""
        if platform.lower() not in ("facebook", "fb", "both"):
            return ""
        raw = [x for x in (ctx["services"] + ctx["headings"]) if x and str(x).strip()]
        if len(raw) < 2:
            return ""
        pick = [self._pick(raw, seed + 11 + i) for i in range(min(3, len(raw)))]
        uniq = []
        for p in pick:
            if p not in uniq:
                uniq.append(p)
        tail = ", ".join(x.lower() for x in uniq[:3])
        return (
            f"\n\nIf you're researching {tail}, save this post and come back when you're ready—"
            f"we're happy to point you in the right direction."
        )

    def _morning_promo(self, ctx: Dict, platform: str, seed: int) -> Tuple[str, str]:
        name = ctx["business_name"]
        opens = [
            f"Good morning! If today is the day you finally move your plans forward, {name} is a message away.",
            f"Rise and shine — busy weeks start with one clear decision. {name} helps you cut through noise with straightforward guidance.",
            f"Morning! Planning something coming up? {name} works with locals who want fewer surprises and a team that actually responds.",
        ]
        mid = self._weave_topics_paragraph(ctx, seed)
        bullets_intro = "A few things worth thinking through early:"
        bullets = self._bullet_points(ctx, seed)
        price_block = ""
        if ctx["prices"]:
            price_block = (
                f"\n\nCurrent spotlight: offers around {ctx['prices'][0]} (details can change—ask us what's available for your dates)."
            )
        ctas = [
            "Message us with your date and headcount—we'll reply with next steps.",
            "Call or DM with what you're planning; we'll help you narrow options fast.",
            "Comment “PLAN” and tell us what you're working on—we'll follow up.",
        ]
        save_line = self._pick(
            [
                "Save this for later if you're still in research mode.",
                "Bookmark this post if you're comparing vendors this month.",
                "If you're not ready today, save it—good planning beats last-minute stress.",
            ],
            seed,
        )
        pre = self._variety_preamble(ctx)
        parts = []
        if pre:
            parts.append(pre.rstrip())
            parts.append("")
        parts.extend(
            [
                self._pick(opens, seed),
                "",
                mid,
                "",
                bullets_intro,
                bullets,
                price_block,
                "",
                self._pick(ctas, seed + 1),
                "",
                save_line,
            ]
        )
        caption = "\n".join(p for p in parts if p is not None)
        caption = caption + self._facebook_semantic_footer(ctx, platform, seed)
        caption = self._append_platform_tail(caption, ctx, platform, seed, "morning_promo")
        img = self._image_prompt(name, ctx, "bright, welcoming, professional local business scene")
        return caption, img

    def _afternoon_tip(self, ctx: Dict, platform: str, seed: int) -> Tuple[str, str]:
        name = ctx["business_name"]
        tips = [
            (
                "Quick tip: book early for the best selection and calmer planning.",
                "When you reserve sooner, you usually get better availability, cleaner logistics, and more time to adjust if something changes.",
            ),
            (
                "Quick tip: before you compare quotes, list what “included” means for you.",
                "Delivery windows, setup, teardown, backup plans, and damage policies are where “cheap” can become expensive—clarity beats a low number that hides gaps.",
            ),
            (
                "Quick tip: share your headcount, date, and access details up front.",
                "Those three data points prevent the back-and-forth that slows everything down—and they help vendors recommend the right fit the first time.",
            ),
        ]
        tip_title, tip_body = tips[seed % len(tips)]

        apply_block = (
            f"How this applies to {name}: we see the smoothest outcomes when customers know what matters most to them "
            f"(budget ceiling, must-haves, nice-to-haves). If you bring that clarity, we can recommend a path that doesn't waste your time."
        )

        story = ""
        if ctx["snippet_a"]:
            story = f"\n\nFrom our website (in plain language):\n“{ctx['snippet_a']}”"
            if ctx["snippet_b"]:
                story += f"\n\nRelated detail: {ctx['snippet_b']}"
        elif ctx["headings"]:
            story = f"\n\nA page topic we get questions about: {ctx['headings'][0]}. If that sounds like you, tell us your situation—we'll translate it into options."

        checklist = (
            "\n\nMini checklist before you commit:\n"
            "• Confirm the date + window\n"
            "• Confirm what's included vs add-ons\n"
            "• Confirm contingency if weather or access changes"
        )

        vp = self._variety_preamble(ctx)
        caption = (
            f"{vp}Tip: {tip_title}\n\n{tip_body}\n\n{apply_block}{story}{checklist}\n\n"
            f"Drop a comment with your biggest question—engagement helps us prioritize what to explain next.\n\n"
            f"— {name}"
        )
        caption = caption + self._facebook_semantic_footer(ctx, platform, seed + 2)
        caption = self._append_platform_tail(caption, ctx, platform, seed + 2, "afternoon_tip")
        img = self._image_prompt(name, ctx, "helpful how-to vibe, clean and friendly")
        return caption, img

    def _evening_proof(self, ctx: Dict, platform: str, seed: int) -> Tuple[str, str]:
        name = ctx["business_name"]
        stories = [
            (
                "Tonight we're grateful for everyone who trusted us this season.",
                "Your events, projects, and “last-minute saves” are why we keep showing up with care—not just equipment or service, but follow-through.",
            ),
            (
                "Love seeing neighbors choose local. Thank you for the support.",
                "When you pick a local team, you're not buying a transaction—you're buying accountability. We feel that responsibility every day.",
            ),
            (
                "Wrapping the day thankful for another round of great customers.",
                "We don't take your trust lightly. Good reviews and referrals don't come from hype; they come from doing the boring details right.",
            ),
        ]
        s1, s2 = stories[seed % len(stories)]

        depth = self._weave_topics_paragraph(ctx, seed + 5)

        questions = [
            "What's the next milestone you're planning for—and what's stressing you most about it?",
            "If you could remove one headache from your planning process, what would it be?",
            "Tell us: what's one thing you'd love to check off your list this month?",
        ]

        vp = self._variety_preamble(ctx)
        caption = (
            f"{vp}{s1}\n\n{s2}\n\n{depth}\n\n"
            f"— {name}\n\n"
            f"{self._pick(questions, seed)}\n\n"
            f"If this resonates, share it with someone who's in planning mode. Word-of-mouth is how local businesses like ours stay healthy."
        )
        caption = caption + self._facebook_semantic_footer(ctx, platform, seed + 4)
        caption = self._append_platform_tail(caption, ctx, platform, seed + 4, "evening_proof")
        img = self._image_prompt(name, ctx, "warm community evening feel, authentic local business")
        return caption, img

    def _append_platform_tail(
        self, caption: str, ctx: Dict, platform: str, seed: int, post_type: str
    ) -> str:
        """Append discovery hashtags: Facebook + Instagram strategies when platform matches."""
        pl = platform.lower()
        out = caption.rstrip()
        if pl in ("facebook", "fb", "both"):
            fb = self._build_facebook_hashtags(ctx, seed, post_type)
            if fb:
                out = f"{out}\n\n{fb}"
        if pl in ("instagram", "ig", "both"):
            ig = self._build_instagram_hashtags(ctx, seed + 5, post_type)
            if ig:
                out = f"{out}\n\n{ig}"
        return out

    def _image_prompt(self, business_name: str, ctx: Dict, mood: str) -> str:
        parts = [f"Photo concept for {business_name}: {mood}."]
        if ctx["services"]:
            parts.append(f"Subtle nod to: {', '.join(ctx['services'][:4])}.")
        if ctx["images"]:
            parts.append(f"Visual ideas: {ctx['images'][0][:120]}.")
        return " ".join(parts)
