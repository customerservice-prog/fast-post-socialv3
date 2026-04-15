"""
SaaS multi-tenant migration: users, user_id scoping, posts_per_day, weekly approval.
Idempotent — safe to run on every startup.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

from werkzeug.security import generate_password_hash


def _iso_week_str(d: datetime | None = None) -> str:
    d = d or datetime.now(timezone.utc)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def apply_saas_migration(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            trial_ends_at TIMESTAMP,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            stripe_price_id TEXT,
            plan_code TEXT DEFAULT 'trial',
            subscription_status TEXT DEFAULT 'trialing',
            subscription_current_period_end TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    def _cols(table: str) -> set:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    acols = _cols("accounts")
    if "user_id" not in acols:
        conn.execute("ALTER TABLE accounts ADD COLUMN user_id INTEGER REFERENCES users(id)")
    if "posts_per_day" not in acols:
        conn.execute("ALTER TABLE accounts ADD COLUMN posts_per_day INTEGER DEFAULT 3")
    if "weekly_approved_iso_week" not in acols:
        conn.execute("ALTER TABLE accounts ADD COLUMN weekly_approved_iso_week TEXT")

    pcols = _cols("posts")
    if "user_id" not in pcols:
        conn.execute("ALTER TABLE posts ADD COLUMN user_id INTEGER REFERENCES users(id)")

    hcols = _cols("post_history")
    if "user_id" not in hcols:
        conn.execute("ALTER TABLE post_history ADD COLUMN user_id INTEGER REFERENCES users(id)")

    ancols = _cols("analytics")
    if "user_id" not in ancols:
        conn.execute("ALTER TABLE analytics ADD COLUMN user_id INTEGER REFERENCES users(id)")

    # Legacy migration user for existing rows without login (orphan data)
    legacy_email = os.getenv("MIGRATION_LEGACY_EMAIL", "legacy@fastpost.local")
    row = conn.execute("SELECT id FROM users WHERE email = ?", (legacy_email,)).fetchone()
    if not row:
        unreachable = generate_password_hash(os.urandom(32).hex())
        trial_end = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()
        conn.execute(
            """
            INSERT INTO users (email, password_hash, display_name, plan_code, subscription_status, trial_ends_at)
            VALUES (?, ?, 'Legacy data', 'agency', 'active', ?)
            """,
            (legacy_email, unreachable, trial_end),
        )
        legacy_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    else:
        legacy_id = row[0]

    conn.execute(
        "UPDATE accounts SET user_id = ? WHERE user_id IS NULL",
        (legacy_id,),
    )
    conn.execute(
        """
        UPDATE posts SET user_id = (
            SELECT a.user_id FROM accounts a WHERE a.id = posts.account_id
        ) WHERE user_id IS NULL
        """
    )
    conn.execute(
        """
        UPDATE post_history SET user_id = (
            SELECT a.user_id FROM accounts a WHERE a.id = post_history.account_id
        ) WHERE user_id IS NULL
        """
    )
    conn.execute(
        """
        UPDATE analytics SET user_id = (
            SELECT a.user_id FROM accounts a WHERE a.id = analytics.account_id
        ) WHERE user_id IS NULL
        """
    )
