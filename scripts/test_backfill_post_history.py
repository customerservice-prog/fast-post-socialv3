#!/usr/bin/env python3
"""Quick test: backfill post_history from published posts."""
from __future__ import annotations

import os
import sys
import tempfile

backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, backend)

from database import Database  # noqa: E402


def main() -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        db = Database(path)
        db.add_account("facebook", "http://fb.com/x", "http://biz.com", "Biz")
        pid = db.add_post(1, "hello world promo", "morning_promo", "2026-01-01 09:00:00", "")
        conn = db.get_conn()
        conn.execute(
            "UPDATE posts SET status = 'published', published_at = '2026-01-02' WHERE id = ?",
            (pid,),
        )
        conn.commit()
        conn.close()

        db2 = Database(path)  # re-init runs backfill
        n = db2.get_post_history_counts().get(1, 0)
        assert n == 1, n
        caps = db2.get_recent_history_captions(1, 5)
        assert caps and "hello" in caps[0]
        print("test_backfill_post_history: ok")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
