"""Fixtures for streamer resolution tests.

Twitch is never contacted: the same in-process `httpx.MockTransport` used by the
Twitch app's tests serves every request here too.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from apps.streamers import services as streamer_services
from apps.twitch.client import OAUTH_TOKEN_URL, TwitchClient
from apps.twitch.tests.conftest import (
    FAKE_APP_TOKEN,
    HELIX_USERS_URL,
    RecordingTransport,
    json_response,
    routed_transport,
    token_payload,
)

__all__ = [
    "FAKE_APP_TOKEN",
    "HELIX_USERS_URL",
    "OAUTH_TOKEN_URL",
    "RecordingTransport",
    "json_response",
    "routed_transport",
]


def twitch_user(
    *,
    user_id: str = "37402112",
    login: str = "shroud",
    display_name: str = "shroud",
    profile_image_url: str = "https://static-cdn.example/shroud-profile.png",
    broadcaster_type: str = "partner",
    description: str = "Professional gamer.",
) -> dict[str, Any]:
    """One entry as Twitch returns it, including fields ClipperStash ignores."""
    return {
        "id": user_id,
        "login": login,
        "display_name": display_name,
        "type": "",
        "broadcaster_type": broadcaster_type,
        "description": description,
        "profile_image_url": profile_image_url,
        "offline_image_url": "https://static-cdn.example/shroud-offline.png",
        "view_count": 123456,
        "created_at": "2012-04-27T00:00:00Z",
    }


def lookup_transport(users: list[dict[str, Any]] | None = None) -> RecordingTransport:
    """A transport that answers the app-token request and one users lookup."""
    return routed_transport(
        {
            OAUTH_TOKEN_URL: json_response(
                200, token_payload(access_token=FAKE_APP_TOKEN, refresh_token=None)
            ),
            HELIX_USERS_URL: json_response(
                200, {"data": [twitch_user()] if users is None else users}
            ),
        }
    )


@pytest.fixture
def make_client() -> Callable[[RecordingTransport], TwitchClient]:
    """A Twitch client wired to a mock transport."""

    def factory(transport: RecordingTransport) -> TwitchClient:
        return TwitchClient(transport=transport)

    return factory


@pytest.fixture
def resolve_with() -> Callable[[RecordingTransport, str], Any]:
    """Run the resolution service against a mock transport."""

    def run(transport: RecordingTransport, submitted: str) -> Any:
        return streamer_services.resolve_streamer(
            submitted, client=TwitchClient(transport=transport)
        )

    return run


@pytest.fixture
def patch_streamer_client(monkeypatch) -> Callable[[RecordingTransport], None]:
    """Route the clients built beneath the API view through a mock transport."""

    def apply(transport: RecordingTransport) -> None:
        from apps.twitch import services as twitch_services

        monkeypatch.setattr(
            twitch_services,
            "TwitchClient",
            lambda *args, **kwargs: TwitchClient(transport=transport),
        )

    return apply
