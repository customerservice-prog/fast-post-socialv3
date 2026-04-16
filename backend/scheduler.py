"""
FastPost Social v3 - Post Scheduler

Posts at 9 AM, 1 PM, and 6 PM (with per-account random ±15-minute jitter so all
accounts don't fire at the exact same second — human-like spread).

Weekly approval auto-publishes today's drafts at each slot.
One-click /api/scheduler/trigger-today publishes ALL of today's pending posts
immediately with randomized inter-post delays.
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
import random
import time
import pytz
import logging
from publish_service import publish_post_with_deps
from subscription_limits import (
    account_week_approved_for_current_week,
    effective_posts_per_day_for_account,
)

logger = logging.getLogger(__name__)


class PostScheduler:
    # The three daily slots (hour, minute) in Eastern time
    DAILY_SLOTS = [
        (9, 0),   # Morning post
        (13, 0),  # Afternoon post
        (18, 0),  # Evening post
    ]
    # Random jitter range ±minutes per account (avoids all accounts posting at once)
    JITTER_MINUTES = 15

    def __init__(self, db, ai_gen, poster, post_executor, timezone: str = "America/New_York"):
        self.db = db
        self.ai_gen = ai_gen
        self.poster = poster
        self.post_executor = post_executor
        self.timezone = pytz.timezone(timezone)
        self.scheduler = BackgroundScheduler(timezone=self.timezone)
        self._setup_jobs()

    def _setup_jobs(self):
        """Configure scheduled jobs — three daily posting slots + morning generation + recrawl."""

        # Morning generation at 6 AM (before first post slot at 9 AM)
        self.scheduler.add_job(
            func=self._generate_daily_drafts,
            trigger=CronTrigger(hour=6, minute=0, timezone=self.timezone),
            id="daily_post_generation",
            name="Generate Daily Post Drafts (6 AM)",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Re-crawl at 5:30 AM (before generation)
        self.scheduler.add_job(
            func=self._recrawl_all,
            trigger=CronTrigger(hour=5, minute=30, timezone=self.timezone),
            id="daily_recrawl",
            name="Daily Website Recrawl",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Three auto-publish slots: 9 AM, 1 PM, 6 PM
        slot_names = ["morning", "afternoon", "evening"]
        for idx, (hour, minute) in enumerate(self.DAILY_SLOTS):
            self.scheduler.add_job(
                func=self._auto_publish_slot,
                kwargs={"slot_index": idx},
                trigger=CronTrigger(hour=hour, minute=minute, timezone=self.timezone),
                id=f"auto_publish_{slot_names[idx]}",
                name=f"Auto-Publish {slot_names[idx].title()} Slot ({hour:02d}:{minute:02d})",
                replace_existing=True,
                misfire_grace_time=3600,
            )

    def _auto_publish_slot(self, slot_index: int = 0):
        """
        Called at 9 AM / 1 PM / 6 PM.
        Publishes the matching slot post for each approved account, with per-account
        random jitter delay so posts stagger naturally (anti-bot spread).
        """
        slot_names = ["morning_promo", "afternoon_tip", "evening_proof"]
        target_type = slot_names[slot_index] if slot_index < len(slot_names) else None
        logger.info("[Scheduler] Auto-publish slot %d (%s) starting", slot_index, target_type)

        accounts = self.db.get_all_accounts()
        if not accounts:
            return

        for account in accounts:
            try:
                if not account_week_approved_for_current_week(account):
                    continue
                uid = account.get("user_id")
                user_row = self.db.get_user_by_id(int(uid)) if uid is not None else None
                if not user_row:
                    continue
                n = effective_posts_per_day_for_account(user_row, account)

                # Per-account random jitter: 0 to JITTER_MINUTES*60 seconds
                jitter_s = random.randint(0, self.JITTER_MINUTES * 60)
                logger.info(
                    "[Scheduler] Account %s — jitter delay %ds before slot publish",
                    account["id"], jitter_s
                )
                time.sleep(jitter_s)

                pending = self.db.get_todays_pending_for_account(int(account["id"]))

                # Filter to the matching post type for this slot (if we have typed drafts)
                if target_type:
                    typed = [p for p in pending if p.get("post_type") == target_type]
                    candidates = typed if typed else pending
                else:
                    candidates = pending

                # Publish one post per slot per account
                for p in candidates[:1]:
                    ok, err, _meth = publish_post_with_deps(
                        self.db, self.poster, self.post_executor, int(p["id"]),
                    )
                    if ok:
                        logger.info(
                            "[Scheduler] Slot %d auto-published post_id=%s for account %s",
                            slot_index, p["id"], account["id"],
                        )
                    else:
                        logger.warning(
                            "[Scheduler] Slot %d auto-publish failed post_id=%s: %s",
                            slot_index, p["id"], err[:200],
                        )
            except Exception as e:
                logger.error(
                    "[Scheduler] Slot %d error for account %s: %s",
                    slot_index, account.get("id"), e
                )

    def _generate_daily_drafts(self):
        """
        Auto-called at 6 AM.
        Generates AI post drafts for ALL linked accounts.
        Posts stay as 'pending' until weekly-approved or user taps Post now.
        """
        logger.info(f"[Scheduler] Starting daily post generation at {datetime.now()}")
        accounts = self.db.get_all_accounts()
        if not accounts:
            logger.info("[Scheduler] No accounts found, skipping generation")
            return

        generated_count = 0
        for account in accounts:
            try:
                uid = account.get("user_id")
                user_row = self.db.get_user_by_id(int(uid)) if uid is not None else None
                if not user_row:
                    logger.warning("[Scheduler] Skipping account %s — no user row", account.get("id"))
                    continue

                n = effective_posts_per_day_for_account(user_row, account)
                crawl_data = self.db.get_crawl_data(account["id"])
                recent = self.db.get_recent_history_captions(account["id"], 30)
                posts = self.ai_gen.generate_daily_posts(
                    business_name=account["business_name"],
                    business_url=account["business_url"],
                    platform=account["platform"],
                    crawl_data=crawl_data,
                    recent_published_captions=recent,
                    num_posts=n,
                )
                for post in posts:
                    self.db.add_post(
                        account_id=account["id"],
                        caption=post["caption"],
                        post_type=post["type"],
                        scheduled_time=post["scheduled_time"],
                        image_prompt=post.get("image_prompt", ""),
                        user_id=int(uid),
                    )
                    generated_count += 1
                logger.info(
                    f"[Scheduler] Generated {len(posts)} posts for {account['business_name']}"
                )
            except Exception as e:
                logger.error(
                    f"[Scheduler] Error generating posts for account {account['id']}: {e}"
                )

        logger.info(
            f"[Scheduler] Daily generation complete. Total posts created: {generated_count}"
        )

    def trigger_today_all(self) -> dict:
        """
        One-click: publish ALL of today's pending drafts for all weekly-approved accounts.
        Posts are randomized in order and spaced with human-like delays (15-90 seconds between posts).
        Returns a summary dict: {published: int, failed: int, errors: list}.
        """
        logger.info("[Scheduler] trigger_today_all: one-click publish all today's posts")
        accounts = self.db.get_all_accounts()
        published = 0
        failed = 0
        errors = []

        # Collect all pending posts across all approved accounts
        all_pending = []
        for account in accounts:
            if not account_week_approved_for_current_week(account):
                continue
            pending = self.db.get_todays_pending_for_account(int(account["id"]))
            for p in pending:
                all_pending.append(p)

        # Randomize post order (human-like)
        random.shuffle(all_pending)

        for i, p in enumerate(all_pending):
            # Inter-post delay: 15-90 seconds random gap (anti-bot: not machine-speed)
            if i > 0:
                gap = random.randint(15, 90)
                logger.info("[Scheduler] trigger_today_all: waiting %ds before next post", gap)
                time.sleep(gap)

            ok, err, _meth = publish_post_with_deps(
                self.db, self.poster, self.post_executor, int(p["id"]),
            )
            if ok:
                published += 1
                logger.info("[Scheduler] trigger_today_all: published post_id=%s", p["id"])
            else:
                failed += 1
                short_err = (err or "unknown")[:200]
                errors.append({"post_id": p["id"], "error": short_err})
                logger.warning(
                    "[Scheduler] trigger_today_all: failed post_id=%s: %s", p["id"], short_err
                )

        return {"published": published, "failed": failed, "errors": errors}

    def _recrawl_all(self):
        """Auto-called at 5:30 AM. Re-crawls all linked business websites."""
        from crawler import BusinessCrawler
        crawler = BusinessCrawler()
        logger.info(f"[Scheduler] Starting daily recrawl at {datetime.now()}")
        accounts = self.db.get_all_accounts()
        for account in accounts:
            try:
                result = crawler.crawl(account["business_url"])
                self.db.update_crawl_data(account["id"], result)
                logger.info(
                    f"[Scheduler] Recrawled {account['business_url']}: {result.get('pages_count', 0)} pages"
                )
            except Exception as e:
                logger.error(f"[Scheduler] Crawl error for {account['business_url']}: {e}")

    def start(self):
        """Start the scheduler"""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("[Scheduler] Started -- slots: 9 AM, 1 PM, 6 PM (+-15min jitter per account)")

    def stop(self):
        """Stop the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("[Scheduler] Stopped")

    def is_running(self) -> bool:
        return self.scheduler.running

    def next_run(self) -> str:
        """Get the next scheduled run time as a string (next auto-publish slot)."""
        slot_names = ["morning", "afternoon", "evening"]
        soonest = None
        soonest_label = ""
        for name in slot_names:
            job = self.scheduler.get_job(f"auto_publish_{name}")
            if job and job.next_run_time:
                if soonest is None or job.next_run_time < soonest:
                    soonest = job.next_run_time
                    soonest_label = name.title()
        if soonest:
            return f"{soonest_label}: {soonest.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        job = self.scheduler.get_job("daily_post_generation")
        if job and job.next_run_time:
            return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z")
        return "Not scheduled"

    def trigger_now(self):
        """Manually trigger post generation immediately (for testing)."""
        logger.info("[Scheduler] Manual trigger initiated")
        self._generate_daily_drafts()
