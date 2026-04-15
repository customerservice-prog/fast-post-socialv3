"""
Plan limits for FastPost SaaS: max linked businesses and max posts per business per day.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

# plan_code values stored in DB
PLAN_STARTER = "starter"
PLAN_GROWTH = "growth"
PLAN_AGENCY = "agency"
PLAN_TRIAL = "trial"
PLAN_FREE = "free"


def _parse_dt(val: Any) -> Optional[datetime]:
    if val is None or val == "":
        return None
    try:
        s = str(val).replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except (ValueError, TypeError):
        return None


def trial_still_valid(user_row: Dict[str, Any]) -> bool:
    te = _parse_dt(user_row.get("trial_ends_at"))
    if not te:
        return False
    return datetime.now(timezone.utc) <= te


def effective_plan_code(user_row: Dict[str, Any]) -> str:
    """
    Resolved plan for limits: starter | growth | agency, trial (app signup), or free.
    Paid Stripe plans keep their limits while subscription status is active or trialing.
    """
    raw = (user_row.get("plan_code") or PLAN_TRIAL).lower()
    st = (user_row.get("subscription_status") or "").lower()

    if raw in (PLAN_STARTER, PLAN_GROWTH, PLAN_AGENCY) and st in ("active", "trialing"):
        return raw

    if st in ("canceled", "unpaid", "past_due", "incomplete_expired"):
        if trial_still_valid(user_row):
            return PLAN_TRIAL
        return PLAN_FREE

    if raw == PLAN_TRIAL and trial_still_valid(user_row):
        return PLAN_TRIAL

    if trial_still_valid(user_row) and raw not in (PLAN_STARTER, PLAN_GROWTH, PLAN_AGENCY):
        return PLAN_TRIAL

    return PLAN_FREE


def max_businesses_for_plan(plan: str) -> Optional[int]:
    """None means unlimited."""
    if plan == PLAN_STARTER or plan == PLAN_FREE or plan == PLAN_TRIAL:
        return 1
    if plan == PLAN_GROWTH:
        return 3
    if plan == PLAN_AGENCY:
        return None
    return 1


def max_posts_per_day_for_plan(plan: str) -> int:
    if plan == PLAN_STARTER or plan == PLAN_FREE or plan == PLAN_TRIAL:
        return 1
    if plan in (PLAN_GROWTH, PLAN_AGENCY):
        return 3
    return 1


def effective_posts_per_day_for_account(
    user_row: Optional[Dict[str, Any]], account_row: Dict[str, Any]
) -> int:
    if not user_row:
        user_row = {}
    plan = effective_plan_code(user_row)
    cap = max_posts_per_day_for_plan(plan)
    try:
        want = int(account_row.get("posts_per_day") or 3)
    except (TypeError, ValueError):
        want = 3
    want = max(1, min(3, want))
    return min(want, cap)


def can_add_business(user_row: Dict[str, Any], current_count: int) -> Tuple[bool, str]:
    plan = effective_plan_code(user_row)
    mx = max_businesses_for_plan(plan)
    if mx is None:
        return True, ""
    if current_count >= mx:
        return False, (
            f"Your plan allows up to {mx} linked business(es). "
            "Upgrade on the Pricing page to add more."
        )
    return True, ""


def current_iso_week_str_utc() -> str:
    d = datetime.now(timezone.utc)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def account_week_approved_for_current_week(account_row: Dict[str, Any]) -> bool:
    w = (account_row.get("weekly_approved_iso_week") or "").strip()
    if not w:
        return False
    return w == current_iso_week_str_utc()
