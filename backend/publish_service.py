"""
Shared publish path for HTTP "Post now" and scheduler auto-publish.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Callable, Dict, Optional, Tuple

import caption_dedup
import facebook_graph
import post_image
from stealth_poster import (
    StealthPoster,
    PROFILES_DIR,
    profile_has_headless_session_data,
    running_in_paas,
)

logger = logging.getLogger(__name__)


def post_via_facebook_graph(account: dict) -> bool:
    plat = (account.get("platform") or "").lower()
    if plat not in ("facebook", "fb", "both"):
        return False
    return bool(account.get("fb_page_id") and account.get("fb_page_access_token"))


def publish_post_with_deps(
    db: Any,
    poster: StealthPoster,
    post_executor: ThreadPoolExecutor,
    post_id: int,
    *,
    timeout_s: Optional[int] = None,
) -> Tuple[bool, str, str]:
    """
    Returns (ok, error_message, method) where method is 'facebook_graph', 'playwright', or ''.
    """
    post = db.get_post(post_id)
    if not post:
        return False, "Post not found", ""

    account = db.get_account(post["account_id"])
    if not account:
        return False, "Account not found", ""

    account_id = int(account["id"])

    def _record() -> None:
        label = caption_dedup.post_type_display(post.get("post_type") or "")
        kw = caption_dedup.extract_keywords(post.get("caption") or "")
        uid = post.get("user_id")
        if uid is None:
            uid = db.resolve_user_id_for_account(account_id)
        db.insert_post_history(
            account_id,
            post_id,
            label,
            post.get("caption") or "",
            caption_dedup.keywords_to_json(kw),
            user_id=uid,
        )
        db.mark_post_published(post_id)

    if post_via_facebook_graph(account):
        img_bytes = post_image.render_share_image_jpeg(
            account.get("business_name") or "",
            post.get("caption") or "",
            post.get("post_type") or "",
        )
        ok, err_msg = facebook_graph.post_page_photo(
            str(account["fb_page_id"]),
            account["fb_page_access_token"],
            post["caption"],
            img_bytes,
        )
        if not ok:
            logger.warning(
                "Facebook photo post failed (%s); retrying as text-only feed post",
                err_msg[:300],
            )
            ok, err_msg = facebook_graph.post_page_feed(
                str(account["fb_page_id"]),
                account["fb_page_access_token"],
                post["caption"],
            )
        if ok:
            _record()
            return True, "", "facebook_graph"
        return False, err_msg or "Facebook API post failed", ""

    plat = (account.get("platform") or "").lower()
    prof = (PROFILES_DIR / f"profile_{account['id']}").resolve()
    has_browser_session = profile_has_headless_session_data(prof)
    if running_in_paas() and not has_browser_session:
        if plat in ("facebook", "fb", "both") and facebook_graph.facebook_oauth_configured():
            return (
                False,
                "No Facebook Page token saved for this account. Connect Facebook under Accounts.",
                "",
            )
        return (
            False,
            "Headless server has no browser session for this account. Upload Session JSON under Accounts.",
            "",
        )

    t = timeout_s if timeout_s is not None else int(os.getenv("POST_TIMEOUT_SECONDS", "840"))
    try:
        fut = post_executor.submit(
            poster.post,
            account["platform"],
            account["page_url"],
            post["caption"],
            account["id"],
        )
        result = fut.result(timeout=t)
    except FutureTimeout:
        return False, f"Posting ran longer than {t}s and was stopped.", ""
    except Exception as e:
        logger.exception("publish_post_with_deps crashed")
        return False, str(e) or "Posting failed unexpectedly", ""

    if result.get("success"):
        _record()
        return True, "", "playwright"
    return False, str(result.get("error") or "Unknown error"), ""
