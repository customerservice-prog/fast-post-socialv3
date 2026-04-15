"""Flask-Login user model for FastPost."""

from __future__ import annotations

from typing import Any, Dict, Optional

from flask_login import UserMixin


class User(UserMixin):
    def __init__(self, row: Dict[str, Any]):
        self.id: int = int(row["id"])
        self.email: str = str(row.get("email") or "")
        self.display_name: str = str(row.get("display_name") or "")
        self._row = dict(row)

    def refresh(self, row: Dict[str, Any]) -> None:
        self._row = dict(row)
        self.email = str(row.get("email") or "")
        self.display_name = str(row.get("display_name") or "")

    def to_public_dict(self) -> Dict[str, Any]:
        r = self._row
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "plan_code": r.get("plan_code"),
            "subscription_status": r.get("subscription_status"),
            "trial_ends_at": r.get("trial_ends_at"),
        }
