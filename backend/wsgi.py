"""WSGI entry for Gunicorn.

Railway healthchecks need the server to accept connections quickly. Importing
`app` pulls in Flask, DB, scheduler, publish pipeline, etc. — that can exceed
the window where the platform treats the replica as "up". We load the real app
on first request (double-checked lock) so Gunicorn can bind immediately.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

_logger = logging.getLogger(__name__)

_init_lock = threading.Lock()
_flask_app = None
_scheduler_started = False


def _ensure_app():
    """Import and start scheduler once; safe under gthread concurrent first requests."""
    global _flask_app, _scheduler_started
    if _flask_app is not None:
        return _flask_app
    with _init_lock:
        if _flask_app is not None:
            return _flask_app
        from app import app as flask_app
        from app import scheduler

        if not _scheduler_started:
            try:
                scheduler.start()
            except Exception:
                _logger.exception(
                    "APScheduler failed to start — API will still serve /api/health"
                )
            _scheduler_started = True
        _flask_app = flask_app
    return _flask_app


class _LazyWSGIApp:
    """WSGI callable that forwards to Flask after one-time heavy import."""

    def __call__(self, environ: dict, start_response: Any):
        return _ensure_app()(environ, start_response)

    def __getattr__(self, name: str) -> Any:
        return getattr(_ensure_app(), name)


application = _LazyWSGIApp()
