"""Shared pytest configuration.

The suite never contacts Twitch. Every Twitch request is served by an in-process
`httpx.MockTransport`, and the credentials below are deliberately fake so that a
leak into a response, log or exception is obvious.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache

# Recognizable placeholders. If any of these strings turns up in a response body
# or an error message, a test asserts on it directly.
FAKE_CLIENT_ID = "test-client-id"
FAKE_CLIENT_SECRET = "FAKE-CLIENT-SECRET-do-not-leak-8f3a21"
FAKE_REDIRECT_URI = "http://localhost:8000/api/twitch/oauth/callback/"
FAKE_FRONTEND_URL = "http://localhost:3000"


@pytest.fixture(autouse=True)
def twitch_test_settings(settings):
    """Configure a fake Twitch application and an in-memory cache."""
    settings.TWITCH_CLIENT_ID = FAKE_CLIENT_ID
    settings.TWITCH_CLIENT_SECRET = FAKE_CLIENT_SECRET
    settings.TWITCH_REDIRECT_URI = FAKE_REDIRECT_URI
    settings.FRONTEND_BASE_URL = FAKE_FRONTEND_URL
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "clipperstash-tests",
        }
    }
    cache.clear()
    yield
    cache.clear()
