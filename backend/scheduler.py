"""
FastPost Social v3 - Post Scheduler
Uses APScheduler to auto-generate posts daily at 7 AM
The scheduler generates draft posts - humans still approve before posting
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import pytz
import logging

logger = logging.getLogger(__name__)


class PostScheduler:
    def __init__(self, db, ai_gen, timezone: str = "America/New_York"):
        self.db = db
        self.ai_gen = ai_gen
        self.timezone = pytz.timezone(timezone)
        self.scheduler = BackgroundScheduler(timezone=self.timezone)
        self._setup_jobs()

    def _setup_jobs(self):
        """Configure scheduled jobs"""
        self.scheduler.add_job(
            func=self._generate_daily_drafts,
            trigger=CronTrigger(hour=7, minute=0, timezone=self.timezone),
            id="daily_post_generation",
            name="Generate Daily Post Drafts",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        self.scheduler.add_job(
            func=self._recrawl_all,
            trigger=CronTrigger(hour=6, minute=30, timezone=self.timezone),
            id="daily_recrawl",
            name="Daily Website Recrawl",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    def _generate_daily_drafts(self):
        """
        Auto-called at 7 AM.
        Generates AI post drafts for ALL linked accounts.
        Posts stay as 'pending' until the user manually approves them.
        """
        logger.info(f"[Scheduler] Starting daily post generation at {datetime.now()}")

        accounts = self.db.get_all_accounts()
        if not accounts:
            logger.info("[Scheduler] No accounts found, skipping generation")
            return

        generated_count = 0
        for account in accounts:
            try:
                crawl_data = self.db.get_crawl_data(account["id"])
                posts = self.ai_gen.generate_daily_posts(
                    business_name=account["business_name"],
                    business_url=account["business_url"],
                    platform=account["platform"],
                    crawl_data=crawl_data,
                )
                for post in posts:
                    self.db.add_post(
                        account_id=account["id"],
                        caption=post["caption"],
                        post_type=post["type"],
                        scheduled_time=post["scheduled_time"],
                        image_prompt=post.get("image_prompt", ""),
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

    def _recrawl_all(self):
        """Auto-called at 6:30 AM. Re-crawls all linked business websites."""
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
            logger.info("[Scheduler] Started")

    def stop(self):
        """Stop the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("[Scheduler] Stopped")

    def is_running(self) -> bool:
        return self.scheduler.running

    def next_run(self) -> str:
        """Get the next scheduled run time as a string"""
        job = self.scheduler.get_job("daily_post_generation")
        if job and job.next_run_time:
            return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z")
        return "Not scheduled"

    def trigger_now(self):
        """Manually trigger post generation immediately (for testing)"""
        logger.info("[Scheduler] Manual trigger initiated")
        self._generate_daily_drafts()
