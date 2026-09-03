"""Celery application for the ClipperStash backend.

No periodic schedule and no monitoring work is registered yet; this module only
establishes the worker configuration that later milestones will build on.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clipperstash.settings")

app = Celery("clipperstash")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(name="clipperstash.ping")
def ping() -> str:
    """Smoke-test task used to verify that a worker is wired up correctly."""
    return "pong"
