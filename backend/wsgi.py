"""WSGI entry for Gunicorn — scheduler runs in the worker process."""
from app import app, scheduler

scheduler.start()

application = app
