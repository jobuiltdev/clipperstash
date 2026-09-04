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
def no_real_websocket(monkeypatch):
    """Make a live WebSocket connection impossible for the whole suite.

    Every EventSub test drives a scripted in-process socket. If any code path
    ever reaches the real client, this fails loudly instead of dialling Twitch.
    """
    import websockets.sync.client

    def refuse(*args, **kwargs):
        raise AssertionError("Tests must never open a real WebSocket connection.")

    monkeypatch.setattr(websockets.sync.client, "connect", refuse)
    monkeypatch.setattr("apps.twitch.eventsub.client.websocket_connect", refuse)


@pytest.fixture(autouse=True)
def no_real_twitch_http(monkeypatch):
    """Make a live Twitch HTTP request impossible for the whole suite.

    Every test drives `TwitchClient` through an in-process `httpx.MockTransport`.
    A client built without one would talk to Twitch for real, so reaching the
    network is turned into a loud failure rather than a silent request. This
    catches the case where a module imported `TwitchClient` directly and a test
    patched only some other module's reference to it.
    """
    from apps.twitch.client import TwitchClient

    original = TwitchClient._send

    def guarded(self, method, url, **kwargs):
        if self._transport is None:
            raise AssertionError(
                "Tests must never make a real Twitch HTTP request; "
                f"a client with no mock transport tried {method} {url}."
            )
        return original(self, method, url, **kwargs)

    monkeypatch.setattr(TwitchClient, "_send", guarded)


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
