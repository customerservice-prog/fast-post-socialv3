"""
Optional transactional email via SMTP (SendGrid, Mailgun SMTP, Gmail app password, etc.).
If MAIL_SERVER is unset, password resets are logged only.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def mail_is_configured() -> bool:
    return bool((os.getenv("MAIL_SERVER") or "").strip())


def _mail_from() -> str:
    return (os.getenv("MAIL_DEFAULT_SENDER") or os.getenv("MAIL_FROM") or "").strip()


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def send_password_reset_email(to_email: str, reset_link: str, app_name: str = "FastPost Social") -> Tuple[bool, str]:
    """
    Send a plain-text password reset message. Returns (ok, error_detail_for_logs).
    """
    server = (os.getenv("MAIL_SERVER") or "").strip()
    if not server:
        return False, "MAIL_SERVER not set"

    mail_from = _mail_from()
    if not mail_from:
        return False, "MAIL_DEFAULT_SENDER or MAIL_FROM not set"

    user = (os.getenv("MAIL_USERNAME") or "").strip()
    password = (os.getenv("MAIL_PASSWORD") or "").strip()
    port = _int_env("MAIL_PORT", 587)
    use_tls = _bool_env("MAIL_USE_TLS", port in (587, 25))
    use_ssl = _bool_env("MAIL_USE_SSL", port == 465)

    subject = f"Reset your {app_name} password"
    body = (
        f"You requested a password reset for {app_name}.\n\n"
        f"Open this link to choose a new password (expires in a few hours):\n\n"
        f"{reset_link}\n\n"
        f"If you did not request this, you can ignore this email.\n"
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = to_email
    msg.set_content(body)

    try:
        if use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(server, port, timeout=30, context=context) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(server, port, timeout=30) as smtp:
                if use_tls:
                    context = ssl.create_default_context()
                    smtp.starttls(context=context)
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
    except Exception as e:
        err = str(e) or type(e).__name__
        logger.exception("SMTP send failed for password reset to %s", to_email)
        return False, err

    logger.info("Password reset email sent to %s", to_email)
    return True, ""
