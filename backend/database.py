"""
FastPost Social v3 - Database Handler
SQLite database for accounts, posts, crawl data, and analytics
"""

import sqlite3
import json
import os
from pathlib import Path
from datetime import date
from typing import Optional, List, Dict


DB_PATH = os.getenv("DATABASE_PATH", "fastpost.db")

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
        self.db_path = db_path
        Path(db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        """Create all tables if they don't exist"""
        conn = self.get_conn()
        c = conn.cursor()
        c.executescript(_SCHEMA)
        conn.commit()
        conn.close()

    # ── ACCOUNTS ─────────────────────────────────────────────────────────────

    def add_account(
        self,
        platform: str,
        page_url: str,
        business_url: str,
        business_name: str,
        session_data: Optional[str] = None,
    ) -> int:
        conn = self.get_conn()
        c = conn.cursor()
        c.execute(
            """INSERT INTO accounts (platform, page_url, business_url, business_name, session_data)
               VALUES (?, ?, ?, ?, ?)""",
            (platform, page_url, business_url, business_name, session_data),
        )
        account_id = c.lastrowid
        conn.commit()
        conn.close()
        return account_id

    def get_account(self, account_id: int) -> Optional[Dict]:
        conn = self.get_conn()
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_all_accounts(self) -> List[Dict]:
        conn = self.get_conn()
        rows = conn.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def delete_account(self, account_id: int):
        conn = self.get_conn()
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
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

    # ── POSTS ─────────────────────────────────────────────────────────────────

    def add_post(
        self,
        account_id: int,
        caption: str,
        post_type: str,
        scheduled_time: str,
        image_prompt: str = "",
    ) -> int:
        conn = self.get_conn()
        c = conn.cursor()
        c.execute(
            """INSERT INTO posts (account_id, caption, post_type, scheduled_time, image_prompt)
               VALUES (?, ?, ?, ?, ?)""",
            (account_id, caption, post_type, scheduled_time, image_prompt),
        )
        post_id = c.lastrowid
        conn.commit()
        conn.close()
        return post_id

    def get_post(self, post_id: int) -> Optional[Dict]:
        conn = self.get_conn()
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_todays_queue(self) -> List[Dict]:
        today = date.today().strftime("%Y-%m-%d")
        conn = self.get_conn()
        rows = conn.execute(
            """SELECT p.*, a.business_name, a.platform, a.page_url
               FROM posts p
               JOIN accounts a ON p.account_id = a.id
               WHERE p.scheduled_time LIKE ? AND p.status = 'pending'
               ORDER BY p.scheduled_time ASC""",
            (f"{today}%",),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_pending_other_days(self, today_yyyy_mm_dd: str, limit: int = 20) -> List[Dict]:
        """Drafts still pending but scheduled for a different calendar day than today."""
        conn = self.get_conn()
        rows = conn.execute(
            """SELECT p.*, a.business_name, a.platform, a.page_url
               FROM posts p
               JOIN accounts a ON p.account_id = a.id
               WHERE p.status = 'pending' AND p.scheduled_time NOT LIKE ?
               ORDER BY p.scheduled_time DESC
               LIMIT ?""",
            (f"{today_yyyy_mm_dd}%", limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def count_published_today(self) -> int:
        today = date.today().strftime("%Y-%m-%d")
        conn = self.get_conn()
        row = conn.execute(
            """SELECT COUNT(*) AS c FROM posts
               WHERE status = 'published' AND published_at IS NOT NULL
               AND strftime('%Y-%m-%d', published_at) = ?""",
            (today,),
        ).fetchone()
        conn.close()
        return int(row["c"]) if row else 0

    def get_recent_published_all(self, limit: int = 20) -> List[Dict]:
        conn = self.get_conn()
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
        conn.close()
        return [dict(r) for r in rows]

    def update_post_caption(self, post_id: int, caption: str):
        conn = self.get_conn()
        conn.execute("UPDATE posts SET caption = ? WHERE id = ?", (caption, post_id))
        conn.commit()
        conn.close()

    def mark_post_published(self, post_id: int):
        conn = self.get_conn()
        conn.execute(
            "UPDATE posts SET status = 'published', published_at = CURRENT_TIMESTAMP WHERE id = ?",
            (post_id,),
        )
        conn.commit()
        conn.close()

    def delete_post(self, post_id: int):
        conn = self.get_conn()
        conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
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
    ):
        conn = self.get_conn()
        conn.execute(
            """INSERT INTO analytics (post_id, account_id, likes, comments, shares, reach)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (post_id, account_id, likes, comments, shares, reach),
        )
        conn.commit()
        conn.close()

    def get_analytics(self) -> Dict:
        conn = self.get_conn()
        total = conn.execute(
            "SELECT COUNT(*) as total, SUM(likes) as likes, SUM(shares) as shares FROM analytics"
        ).fetchone()
        published = conn.execute(
            "SELECT COUNT(*) as count FROM posts WHERE status='published'"
        ).fetchone()
        conn.close()
        return {
            "total_posts_published": published["count"] if published else 0,
            "total_likes": total["likes"] or 0,
            "total_shares": total["shares"] or 0,
        }

    def get_account_analytics(self, account_id: int) -> Dict:
        conn = self.get_conn()
        rows = conn.execute(
            """SELECT p.post_type, p.caption, p.published_at,
                      a.likes, a.comments, a.shares, a.reach
               FROM posts p
               LEFT JOIN analytics a ON p.id = a.post_id
               WHERE p.account_id = ? AND p.status = 'published'
               ORDER BY p.published_at DESC LIMIT 30""",
            (account_id,),
        ).fetchall()
        conn.close()
        return {"posts": [dict(r) for r in rows]}
