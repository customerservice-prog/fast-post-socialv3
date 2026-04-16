"""
FastPost Social v3 - Database Handler
SQLite database for accounts, posts, crawl data, and analytics
"""

import sqlite3
import json
import logging
import os
import hashlib
import secrets
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from typing import Optional, List, Dict, Any

from werkzeug.security import generate_password_hash

from migrations.saas_v5 import apply_saas_migration

logger = logging.getLogger(__name__)

# Default DB path is next to this file so it does not depend on process cwd (Gunicorn, Railway, etc.).
_BACKEND_DIR = Path(__file__).resolve().parent
_DEFAULT_SQLITE = str(_BACKEND_DIR / "fastpost.db")
DB_PATH = os.getenv("DATABASE_PATH", _DEFAULT_SQLITE)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    page_url TEXT NOT NULL,
    business_url TEXT NOT NULL,
    business_name TEXT NOT NULL,
    session_data TEXT,
    crawl_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    caption TEXT NOT NULL,
    post_type TEXT NOT NULL,
    scheduled_time TEXT NOT NULL,
    image_prompt TEXT,
    status TEXT DEFAULT 'pending',
    published_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS analytics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    reach INTEGER DEFAULT 0,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
);
"""


class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = self._ensure_writable_db_path(db_path)
        Path(self.db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @staticmethod
    def _ensure_writable_db_path(db_path: str) -> str:
        """
        Railway users often set DATABASE_PATH=/data/... without a volume; mkdir then fails and
        the app never binds (healthcheck 503). Fall back to fastpost.db next to this package.
        """
        path = Path(db_path).expanduser()
        if not path.is_absolute():
            path = (_BACKEND_DIR / path).resolve()
        else:
            path = path.resolve()
        parent = path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            fb = _BACKEND_DIR / "fastpost.db"
            logger.warning(
                "Could not create database directory %s (%s); using %s",
                parent,
                exc,
                fb,
            )
            return str(fb)
        return str(path)

    def get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
        return conn

    def init_db(self):
        """Create all tables if they don't exist"""
        conn = self.get_conn()
        c = conn.cursor()
        c.executescript(_SCHEMA)
        self._migrate_accounts_facebook_graph(conn)
        self._migrate_post_history(conn)
        apply_saas_migration(conn)
        self._backfill_post_history_from_published_posts(conn)
        conn.commit()
        conn.close()

    def _migrate_post_history(self, conn):
        """Published post archive for de-duplication and analytics."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS post_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                post_id INTEGER,
                post_type TEXT NOT NULL,
                caption_text TEXT NOT NULL,
                published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                keywords TEXT NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE SET NULL
            )
            """
        )

    def _backfill_post_history_from_published_posts(self, conn) -> None:
        """
        One-time style backfill: copy rows from posts (status=published) into post_history
        when no history row exists for that post_id. Safe to run on every startup (idempotent).
        """
        try:
            from caption_dedup import extract_keywords, keywords_to_json, post_type_display
        except ImportError:
            return
        rows = conn.execute(
            """
            SELECT p.id, p.account_id, p.post_type, p.caption, p.published_at
            FROM posts p
            WHERE p.status = 'published'
            AND NOT EXISTS (
                SELECT 1 FROM post_history h WHERE h.post_id = p.id
            )
            """
        ).fetchall()
        for r in rows:
            cap = r["caption"] or ""
            label = post_type_display(str(r["post_type"] or ""))
            kw = keywords_to_json(extract_keywords(cap))
            pub = r["published_at"]
            uid_row = conn.execute(
                "SELECT user_id FROM accounts WHERE id = ?", (r["account_id"],)
            ).fetchone()
            uid = uid_row["user_id"] if uid_row else None
            hcols = {x[1] for x in conn.execute("PRAGMA table_info(post_history)").fetchall()}
            if "user_id" in hcols and uid is not None:
                conn.execute(
                    """INSERT INTO post_history (account_id, post_id, post_type, caption_text, keywords, published_at, user_id)
                       VALUES (?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?)""",
                    (r["account_id"], r["id"], label, cap, kw, pub, uid),
                )
            else:
                conn.execute(
                    """INSERT INTO post_history (account_id, post_id, post_type, caption_text, keywords, published_at)
                       VALUES (?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))""",
                    (r["account_id"], r["id"], label, cap, kw, pub),
                )

    def _migrate_accounts_facebook_graph(self, conn):
        """Add Facebook Graph API columns for headless posting without Playwright session."""
        rows = conn.execute("PRAGMA table_info(accounts)").fetchall()
        cols = {r[1] for r in rows}
        for col, ddl in (
            ("fb_page_id", "ALTER TABLE accounts ADD COLUMN fb_page_id TEXT"),
            ("fb_page_access_token", "ALTER TABLE accounts ADD COLUMN fb_page_access_token TEXT"),
            ("fb_token_expires_at", "ALTER TABLE accounts ADD COLUMN fb_token_expires_at INTEGER"),
        ):
            if col not in cols:
                conn.execute(ddl)

    # ── ACCOUNTS ─────────────────────────────────────────────────────────────

    def add_account(
        self,
        platform: str,
        page_url: str,
        business_url: str,
        business_name: str,
        user_id: int,
        session_data: Optional[str] = None,
        posts_per_day: int = 3,
    ) -> int:
        conn = self.get_conn()
        c = conn.cursor()
        c.execute(
            """INSERT INTO accounts (platform, page_url, business_url, business_name, session_data, user_id, posts_per_day)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (platform, page_url, business_url, business_name, session_data, user_id, posts_per_day),
        )
        account_id = c.lastrowid
        conn.commit()
        conn.close()
        return account_id

    def get_account(self, account_id: int, user_id: Optional[int] = None) -> Optional[Dict]:
        conn = self.get_conn()
        if user_id is None:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM accounts WHERE id = ? AND user_id = ?", (account_id, user_id)
            ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_accounts_for_user(self, user_id: int) -> List[Dict]:
        conn = self.get_conn()
        rows = conn.execute(
            "SELECT * FROM accounts WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def count_accounts_for_user(self, user_id: int) -> int:
        conn = self.get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM accounts WHERE user_id = ?", (user_id,)
        ).fetchone()
        conn.close()
        return int(row["c"]) if row else 0

    def get_all_accounts(self) -> List[Dict]:
        """All accounts (scheduler / admin). Prefer get_accounts_for_user in API."""
        conn = self.get_conn()
        rows = conn.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_account_posts_per_day(self, account_id: int, user_id: int, posts_per_day: int) -> bool:
        n = max(1, min(3, int(posts_per_day)))
        conn = self.get_conn()
        cur = conn.execute(
            "UPDATE accounts SET posts_per_day = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (n, account_id, user_id),
        )
        conn.commit()
        ok = cur.rowcount > 0
        conn.close()
        return ok

    def set_weekly_approval(self, account_id: int, user_id: int, approved: bool) -> bool:
        week = ""
        if approved:
            d = datetime.now(timezone.utc)
            y, w, _ = d.isocalendar()
            week = f"{y}-W{w:02d}"
        conn = self.get_conn()
        cur = conn.execute(
            "UPDATE accounts SET weekly_approved_iso_week = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (week or None, account_id, user_id),
        )
        conn.commit()
        ok = cur.rowcount > 0
        conn.close()
        return ok

    def delete_account(self, account_id: int, user_id: Optional[int] = None):
        conn = self.get_conn()
        if user_id is None:
            conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        else:
            conn.execute("DELETE FROM accounts WHERE id = ? AND user_id = ?", (account_id, user_id))
        conn.commit()
        conn.close()

    def update_crawl_data(self, account_id: int, crawl_data: dict):
        conn = self.get_conn()
        conn.execute(
            "UPDATE accounts SET crawl_data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(crawl_data), account_id),
        )
        conn.commit()
        conn.close()

    def get_crawl_data(self, account_id: int) -> Optional[dict]:
        conn = self.get_conn()
        row = conn.execute("SELECT crawl_data FROM accounts WHERE id = ?", (account_id,)).fetchone()
        conn.close()
        if row and row["crawl_data"]:
            return json.loads(row["crawl_data"])
        return None

    def save_session(self, account_id: int, session_data: str):
        conn = self.get_conn()
        conn.execute(
            "UPDATE accounts SET session_data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_data, account_id),
        )
        conn.commit()
        conn.close()

    def get_session(self, account_id: int) -> Optional[str]:
        conn = self.get_conn()
        row = conn.execute("SELECT session_data FROM accounts WHERE id = ?", (account_id,)).fetchone()
        conn.close()
        return row["session_data"] if row else None

    def update_facebook_graph_token(
        self,
        account_id: int,
        page_id: str,
        page_access_token: str,
        expires_at: Optional[int] = None,
    ):
        """Store Page id + token from Facebook Login (Graph API). expires_at: Unix seconds or None."""
        conn = self.get_conn()
        conn.execute(
            """UPDATE accounts SET fb_page_id = ?, fb_page_access_token = ?, fb_token_expires_at = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (page_id, page_access_token, expires_at, account_id),
        )
        conn.commit()
        conn.close()

    def clear_facebook_graph_token(self, account_id: int):
        conn = self.get_conn()
        conn.execute(
            """UPDATE accounts SET fb_page_id = NULL, fb_page_access_token = NULL, fb_token_expires_at = NULL,
                   updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (account_id,),
        )
        conn.commit()
        conn.close()

    # ── POSTS ─────────────────────────────────────────────────────────────────

    def resolve_user_id_for_account(self, account_id: int) -> Optional[int]:
        conn = self.get_conn()
        row = conn.execute("SELECT user_id FROM accounts WHERE id = ?", (account_id,)).fetchone()
        conn.close()
        if not row or row["user_id"] is None:
            return None
        return int(row["user_id"])

    def add_post(
        self,
        account_id: int,
        caption: str,
        post_type: str,
        scheduled_time: str,
        image_prompt: str = "",
        user_id: Optional[int] = None,
    ) -> int:
        uid = user_id if user_id is not None else self.resolve_user_id_for_account(account_id)
        conn = self.get_conn()
        c = conn.cursor()
        c.execute(
            """INSERT INTO posts (account_id, caption, post_type, scheduled_time, image_prompt, user_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (account_id, caption, post_type, scheduled_time, image_prompt, uid),
        )
        post_id = c.lastrowid
        conn.commit()
        conn.close()
        return post_id

    def get_post(self, post_id: int, user_id: Optional[int] = None) -> Optional[Dict]:
        conn = self.get_conn()
        if user_id is None:
            row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM posts WHERE id = ? AND user_id = ?", (post_id, user_id)
            ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_todays_queue(self, user_id: Optional[int] = None) -> List[Dict]:
        today = date.today().strftime("%Y-%m-%d")
        conn = self.get_conn()
        if user_id is None:
            rows = conn.execute(
                """SELECT p.*, a.business_name, a.platform, a.page_url
                   FROM posts p
                   JOIN accounts a ON p.account_id = a.id
                   WHERE p.scheduled_time LIKE ? AND p.status = 'pending'
                   ORDER BY p.scheduled_time ASC""",
                (f"{today}%",),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT p.*, a.business_name, a.platform, a.page_url
                   FROM posts p
                   JOIN accounts a ON p.account_id = a.id
                   WHERE p.user_id = ? AND p.scheduled_time LIKE ? AND p.status = 'pending'
                   ORDER BY p.scheduled_time ASC""",
                (user_id, f"{today}%"),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_todays_pending_for_account(self, account_id: int) -> List[Dict]:
        """Pending posts scheduled for today's calendar date (scheduler auto-publish)."""
        today = date.today().strftime("%Y-%m-%d")
        conn = self.get_conn()
        rows = conn.execute(
            """SELECT p.*, a.business_name, a.platform, a.page_url
               FROM posts p
               JOIN accounts a ON p.account_id = a.id
               WHERE p.account_id = ? AND p.scheduled_time LIKE ? AND p.status = 'pending'
               ORDER BY p.scheduled_time ASC""",
            (account_id, f"{today}%"),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_pending_other_days(
        self, today_yyyy_mm_dd: str, limit: int = 20, user_id: Optional[int] = None
    ) -> List[Dict]:
        """Drafts still pending but scheduled for a different calendar day than today."""
        conn = self.get_conn()
        if user_id is None:
            rows = conn.execute(
                """SELECT p.*, a.business_name, a.platform, a.page_url
                   FROM posts p
                   JOIN accounts a ON p.account_id = a.id
                   WHERE p.status = 'pending' AND p.scheduled_time NOT LIKE ?
                   ORDER BY p.scheduled_time DESC
                   LIMIT ?""",
                (f"{today_yyyy_mm_dd}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT p.*, a.business_name, a.platform, a.page_url
                   FROM posts p
                   JOIN accounts a ON p.account_id = a.id
                   WHERE p.user_id = ? AND p.status = 'pending' AND p.scheduled_time NOT LIKE ?
                   ORDER BY p.scheduled_time DESC
                   LIMIT ?""",
                (user_id, f"{today_yyyy_mm_dd}%", limit),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def count_published_today(self, user_id: Optional[int] = None) -> int:
        today = date.today().strftime("%Y-%m-%d")
        conn = self.get_conn()
        if user_id is None:
            row = conn.execute(
                """SELECT COUNT(*) AS c FROM posts
                   WHERE status = 'published' AND published_at IS NOT NULL
                   AND strftime('%Y-%m-%d', published_at) = ?""",
                (today,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT COUNT(*) AS c FROM posts
                   WHERE user_id = ? AND status = 'published' AND published_at IS NOT NULL
                   AND strftime('%Y-%m-%d', published_at) = ?""",
                (user_id, today),
            ).fetchone()
        conn.close()
        return int(row["c"]) if row else 0

    def get_recent_published_all(self, limit: int = 20, user_id: Optional[int] = None) -> List[Dict]:
        conn = self.get_conn()
        if user_id is None:
            rows = conn.execute(
                """SELECT p.id, p.caption, p.post_type, p.scheduled_time, p.published_at,
                          p.created_at, a.business_name, a.platform, a.page_url, a.id AS account_id
                   FROM posts p
                   JOIN accounts a ON p.account_id = a.id
                   WHERE p.status = 'published'
                   ORDER BY datetime(published_at) DESC, p.id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT p.id, p.caption, p.post_type, p.scheduled_time, p.published_at,
                          p.created_at, a.business_name, a.platform, a.page_url, a.id AS account_id
                   FROM posts p
                   JOIN accounts a ON p.account_id = a.id
                   WHERE p.user_id = ? AND p.status = 'published'
                   ORDER BY datetime(published_at) DESC, p.id DESC
                   LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_post_caption(self, post_id: int, caption: str, user_id: Optional[int] = None):
        conn = self.get_conn()
        if user_id is None:
            conn.execute("UPDATE posts SET caption = ? WHERE id = ?", (caption, post_id))
        else:
            conn.execute(
                "UPDATE posts SET caption = ? WHERE id = ? AND user_id = ?",
                (caption, post_id, user_id),
            )
        conn.commit()
        conn.close()

    def mark_post_published(self, post_id: int, user_id: Optional[int] = None):
        conn = self.get_conn()
        if user_id is None:
            conn.execute(
                "UPDATE posts SET status = 'published', published_at = CURRENT_TIMESTAMP WHERE id = ?",
                (post_id,),
            )
        else:
            conn.execute(
                "UPDATE posts SET status = 'published', published_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
                (post_id, user_id),
            )
        conn.commit()
        conn.close()

    def insert_post_history(
        self,
        account_id: int,
        post_id: Optional[int],
        post_type_label: str,
        caption_text: str,
        keywords_json: str,
        user_id: Optional[int] = None,
    ) -> int:
        """Record a successful publish (Graph or browser). keywords_json: JSON array of strings."""
        uid = user_id if user_id is not None else self.resolve_user_id_for_account(account_id)
        conn = self.get_conn()
        c = conn.cursor()
        c.execute(
            """INSERT INTO post_history (account_id, post_id, post_type, caption_text, keywords, user_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (account_id, post_id, post_type_label, caption_text, keywords_json, uid),
        )
        hid = c.lastrowid
        conn.commit()
        conn.close()
        return int(hid)

    def get_recent_history_captions(self, account_id: int, limit: int = 30) -> List[str]:
        """Last N published captions for this account (for draft de-duplication)."""
        conn = self.get_conn()
        rows = conn.execute(
            """SELECT caption_text FROM post_history
               WHERE account_id = ?
               ORDER BY datetime(published_at) DESC, id DESC
               LIMIT ?""",
            (account_id, limit),
        ).fetchall()
        conn.close()
        return [str(r["caption_text"]) for r in rows if r["caption_text"]]

    def get_post_history_counts(self, user_id: Optional[int] = None) -> Dict[int, int]:
        """account_id -> number of rows in post_history."""
        conn = self.get_conn()
        if user_id is None:
            rows = conn.execute(
                "SELECT account_id, COUNT(*) AS c FROM post_history GROUP BY account_id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT account_id, COUNT(*) AS c FROM post_history WHERE user_id = ? GROUP BY account_id",
                (user_id,),
            ).fetchall()
        conn.close()
        return {int(r["account_id"]): int(r["c"]) for r in rows}

    def delete_post(self, post_id: int, user_id: Optional[int] = None):
        conn = self.get_conn()
        if user_id is None:
            conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        else:
            conn.execute("DELETE FROM posts WHERE id = ? AND user_id = ?", (post_id, user_id))
        conn.commit()
        conn.close()

    # ── ANALYTICS ─────────────────────────────────────────────────────────────

    def add_analytics(
        self,
        post_id: int,
        account_id: int,
        likes: int = 0,
        comments: int = 0,
        shares: int = 0,
        reach: int = 0,
        user_id: Optional[int] = None,
    ):
        uid = user_id if user_id is not None else self.resolve_user_id_for_account(account_id)
        conn = self.get_conn()
        conn.execute(
            """INSERT INTO analytics (post_id, account_id, likes, comments, shares, reach, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (post_id, account_id, likes, comments, shares, reach, uid),
        )
        conn.commit()
        conn.close()

    def get_analytics(self, user_id: Optional[int] = None) -> Dict:
        conn = self.get_conn()
        if user_id is None:
            total = conn.execute(
                "SELECT COUNT(*) as total, SUM(likes) as likes, SUM(shares) as shares FROM analytics"
            ).fetchone()
            published = conn.execute(
                "SELECT COUNT(*) as count FROM posts WHERE status='published'"
            ).fetchone()
        else:
            total = conn.execute(
                "SELECT COUNT(*) as total, SUM(likes) as likes, SUM(shares) as shares FROM analytics WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            published = conn.execute(
                "SELECT COUNT(*) as count FROM posts WHERE status='published' AND user_id = ?",
                (user_id,),
            ).fetchone()
        conn.close()
        return {
            "total_posts_published": published["count"] if published else 0,
            "total_likes": total["likes"] or 0,
            "total_shares": total["shares"] or 0,
        }

    def get_account_analytics(self, account_id: int, user_id: Optional[int] = None) -> Dict:
        conn = self.get_conn()
        if user_id is None:
            rows = conn.execute(
                """SELECT p.post_type, p.caption, p.published_at,
                          a.likes, a.comments, a.shares, a.reach
                   FROM posts p
                   LEFT JOIN analytics a ON p.id = a.post_id
                   WHERE p.account_id = ? AND p.status = 'published'
                   ORDER BY p.published_at DESC LIMIT 30""",
                (account_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT p.post_type, p.caption, p.published_at,
                          a.likes, a.comments, a.shares, a.reach
                   FROM posts p
                   LEFT JOIN analytics a ON p.id = a.post_id
                   WHERE p.account_id = ? AND p.user_id = ? AND p.status = 'published'
                   ORDER BY p.published_at DESC LIMIT 30""",
                (account_id, user_id),
            ).fetchall()
        conn.close()
        return {"posts": [dict(r) for r in rows]}

    # ── USERS & AUTH ─────────────────────────────────────────────────────────

    def create_user(
        self,
        email: str,
        password_plain: str,
        display_name: str = "",
        trial_days: int = 7,
    ) -> int:
        email = (email or "").strip().lower()
        ph = generate_password_hash(password_plain)
        trial_end = datetime.now(timezone.utc) + timedelta(days=max(1, int(trial_days)))
        conn = self.get_conn()
        c = conn.cursor()
        c.execute(
            """INSERT INTO users (email, password_hash, display_name, trial_ends_at, plan_code, subscription_status)
               VALUES (?, ?, ?, ?, 'trial', 'trialing')""",
            (email, ph, display_name or "", trial_end.isoformat()),
        )
        uid = c.lastrowid
        conn.commit()
        conn.close()
        return int(uid)

    def set_user_password(self, user_id: int, password_plain: str) -> None:
        ph = generate_password_hash(password_plain)
        conn = self.get_conn()
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?", (ph, user_id)
        )
        conn.commit()
        conn.close()

    def get_user_by_email(self, email: str) -> Optional[Dict]:
        email = (email or "").strip().lower()
        conn = self.get_conn()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        conn = self.get_conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def update_user_subscription_fields(
        self,
        user_id: int,
        *,
        stripe_customer_id: Any = None,
        stripe_subscription_id: Any = None,
        stripe_price_id: Any = None,
        plan_code: Any = None,
        subscription_status: Any = None,
        subscription_current_period_end: Any = None,
        trial_ends_at: Any = None,
    ) -> None:
        conn = self.get_conn()
        fields = []
        vals = []
        mapping = [
            ("stripe_customer_id", stripe_customer_id),
            ("stripe_subscription_id", stripe_subscription_id),
            ("stripe_price_id", stripe_price_id),
            ("plan_code", plan_code),
            ("subscription_status", subscription_status),
            ("subscription_current_period_end", subscription_current_period_end),
            ("trial_ends_at", trial_ends_at),
        ]
        for name, val in mapping:
            if val is not None:
                fields.append(f"{name} = ?")
                vals.append(val)
        if not fields:
            conn.close()
            return
        vals.append(user_id)
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", vals)
        conn.commit()
        conn.close()

    def list_all_users(self) -> List[Dict]:
        conn = self.get_conn()
        rows = conn.execute(
            "SELECT id, email, display_name, created_at, trial_ends_at, stripe_customer_id, "
            "stripe_subscription_id, stripe_price_id, plan_code, subscription_status, subscription_current_period_end "
            "FROM users ORDER BY id ASC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def admin_set_user_plan(
        self,
        user_id: int,
        plan_code: str,
        subscription_status: str = "active",
    ) -> bool:
        conn = self.get_conn()
        cur = conn.execute(
            "UPDATE users SET plan_code = ?, subscription_status = ? WHERE id = ?",
            (plan_code, subscription_status, user_id),
        )
        conn.commit()
        ok = cur.rowcount > 0
        conn.close()
        return ok

    def _hash_reset_token(self, raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def create_password_reset_token(self, user_id: int, ttl_hours: int = 2) -> str:
        raw = secrets.token_urlsafe(32)
        th = self._hash_reset_token(raw)
        exp = datetime.now(timezone.utc) + timedelta(hours=max(1, ttl_hours))
        conn = self.get_conn()
        conn.execute("DELETE FROM password_reset_tokens WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (user_id, th, exp.isoformat()),
        )
        conn.commit()
        conn.close()
        return raw

    def consume_password_reset_token(self, raw_token: str) -> Optional[int]:
        if not raw_token or not str(raw_token).strip():
            return None
        th = self._hash_reset_token(raw_token.strip())
        conn = self.get_conn()
        row = conn.execute(
            "SELECT user_id, expires_at FROM password_reset_tokens WHERE token_hash = ?", (th,)
        ).fetchone()
        if not row:
            conn.close()
            return None
        try:
            exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            conn.close()
            return None
        if datetime.now(timezone.utc) > exp:
            conn.execute("DELETE FROM password_reset_tokens WHERE token_hash = ?", (th,))
            conn.commit()
            conn.close()
            return None
        uid = int(row["user_id"])
        conn.execute("DELETE FROM password_reset_tokens WHERE token_hash = ?", (th,))
        conn.commit()
        conn.close()
        return uid

    def count_all_accounts_global(self) -> int:
        conn = self.get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM accounts").fetchone()
        conn.close()
        return int(row["c"]) if row else 0

    def count_all_published_posts_global(self) -> int:
        conn = self.get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM posts WHERE status = 'published'"
        ).fetchone()
        conn.close()
        return int(row["c"]) if row else 0
