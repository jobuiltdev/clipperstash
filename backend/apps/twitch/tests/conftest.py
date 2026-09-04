"""Mock Twitch transport and fixtures.

No test in this package performs real network I/O: `httpx.MockTransport` answers
every request in-process.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest
from django.utils import timezone

from apps.twitch import services
from apps.twitch.client import (
    HELIX_BASE_URL,
    OAUTH_TOKEN_URL,
    OAUTH_VALIDATE_URL,
    TwitchClient,
)
from apps.twitch.models import TwitchConnection

# Recognizable fake secrets. A leak assertion is only meaningful if the value
# could not plausibly appear by accident.
FAKE_APP_TOKEN = "FAKE-APP-TOKEN-do-not-leak-app0001"
FAKE_ACCESS_TOKEN = "FAKE-ACCESS-TOKEN-do-not-leak-at0001"
FAKE_ACCESS_TOKEN_2 = "FAKE-ACCESS-TOKEN-do-not-leak-at0002"
FAKE_REFRESH_TOKEN = "FAKE-REFRESH-TOKEN-do-not-leak-rt0001"
FAKE_REFRESH_TOKEN_2 = "FAKE-REFRESH-TOKEN-do-not-leak-rt0002"
FAKE_AUTH_CODE = "FAKE-AUTH-CODE-do-not-leak-ac0001"

HELIX_USERS_URL = f"{HELIX_BASE_URL}/users"


class RecordingTransport(httpx.MockTransport):
    """A mock transport that keeps every request it served, for assertions."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        super().__init__(record)

    def request_for(self, url: str) -> httpx.Request:
        for request in self.requests:
            if str(request.url).split("?")[0] == url:
                return request
        raise AssertionError(f"No request was made to {url}")

    @property
    def call_count(self) -> int:
        return len(self.requests)


def routed_transport(routes: dict[str, Any]) -> RecordingTransport:
    """Build a transport from a {url: response-or-callable} mapping.

    A route value may be an `httpx.Response`, a callable taking the request, or
    a list of responses consumed in order (to model a retry).
    """
    remaining = {
        url: list(value) if isinstance(value, list) else value for url, value in routes.items()
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        route = remaining.get(url)
        if route is None:
            raise AssertionError(f"Unexpected Twitch request to {url}")
        if isinstance(route, list):
            if not route:
                raise AssertionError(f"No responses left for {url}")
            route = route.pop(0)
        if callable(route):
            return route(request)
        return route

    return RecordingTransport(handler)


def json_response(status_code: int, payload: Any) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def token_payload(
    *,
    access_token: str = FAKE_ACCESS_TOKEN,
    refresh_token: str | None = FAKE_REFRESH_TOKEN,
    expires_in: int = 14400,
    scope: Any = ("clips:edit", "user:read:chat"),
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "access_token": access_token,
        "expires_in": expires_in,
        "token_type": "bearer",
        "scope": list(scope),
    }
    if refresh_token is not None:
        payload["refresh_token"] = refresh_token
    return payload


def users_payload(
    *,
    user_id: str = "123456",
    login: str = "example",
    display_name: str = "Example",
) -> dict[str, Any]:
    return {"data": [{"id": user_id, "login": login, "display_name": display_name}]}


@pytest.fixture
def make_client() -> Callable[[RecordingTransport], TwitchClient]:
    def factory(transport: RecordingTransport) -> TwitchClient:
        return TwitchClient(transport=transport)

    return factory


@pytest.fixture
def patch_service_client(monkeypatch) -> Callable[[RecordingTransport], None]:
    """Route every client the service layer builds through a mock transport."""

    def apply(transport: RecordingTransport) -> None:
        monkeypatch.setattr(
            services,
            "TwitchClient",
            lambda *args, **kwargs: TwitchClient(transport=transport),
        )

    return apply


class RecordingCache:
    """Wraps the real cache so tests can assert on the TTL a caller requested."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.sets: list[tuple[str, Any, Any]] = []

    def get(self, key: str, default: Any = None) -> Any:
        return self._inner.get(key, default)

    def set(self, key: str, value: Any, timeout: Any = None) -> Any:
        self.sets.append((key, value, timeout))
        return self._inner.set(key, value, timeout)

    def delete(self, key: str) -> Any:
        return self._inner.delete(key)

    def timeout_for(self, key: str) -> Any:
        for recorded_key, _value, timeout in self.sets:
            if recorded_key == key:
                return timeout
        raise AssertionError(f"Nothing was cached under {key}")

    def was_set(self, key: str) -> bool:
        return any(recorded_key == key for recorded_key, _value, _timeout in self.sets)


@pytest.fixture
def recording_cache(monkeypatch) -> RecordingCache:
    from django.core.cache import cache as django_cache

    recorder = RecordingCache(django_cache)
    monkeypatch.setattr(services, "cache", recorder)
    return recorder


@pytest.fixture
def connection(db) -> TwitchConnection:
    return TwitchConnection.objects.create(
        twitch_user_id="123456",
        login="example",
        display_name="Example",
        access_token=FAKE_ACCESS_TOKEN,
        refresh_token=FAKE_REFRESH_TOKEN,
        token_expires_at=timezone.now() + timedelta(hours=4),
        scopes=["clips:edit", "user:read:chat"],
    )


@pytest.fixture
def expired_connection(connection: TwitchConnection) -> TwitchConnection:
    connection.token_expires_at = timezone.now() - timedelta(minutes=5)
    connection.save(update_fields=["token_expires_at"])
    return connection


ALL_FAKE_SECRETS = (
    FAKE_APP_TOKEN,
    FAKE_ACCESS_TOKEN,
    FAKE_ACCESS_TOKEN_2,
    FAKE_REFRESH_TOKEN,
    FAKE_REFRESH_TOKEN_2,
    FAKE_AUTH_CODE,
)


__all__ = [
    "ALL_FAKE_SECRETS",
    "FAKE_ACCESS_TOKEN",
    "FAKE_ACCESS_TOKEN_2",
    "FAKE_APP_TOKEN",
    "FAKE_AUTH_CODE",
    "FAKE_REFRESH_TOKEN",
    "FAKE_REFRESH_TOKEN_2",
    "HELIX_USERS_URL",
    "OAUTH_TOKEN_URL",
    "OAUTH_VALIDATE_URL",
    "RecordingCache",
    "RecordingTransport",
    "json_response",
    "routed_transport",
    "token_payload",
    "users_payload",
]
