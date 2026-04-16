"""WSGI entry for Gunicorn — scheduler runs in the worker process."""
import logging

from app import app, scheduler

_logger = logging.getLogger(__name__)

try:
    scheduler.start()
except Exception:
    _logger.exception("APScheduler failed to start — API will still serve /api/health")

application = app
