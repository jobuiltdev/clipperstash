"""Fixtures for stream observation tests.

Twitch is never contacted: every request is served in process by the same
`httpx.MockTransport` the other apps use.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from apps.monitoring import services as monitoring_services
from apps.streamers.models import Platform, Streamer
from apps.twitch.client import HELIX_BASE_URL, OAUTH_TOKEN_URL, TwitchClient
from apps.twitch.tests.conftest import (
    FAKE_APP_TOKEN,
    RecordingTransport,
    json_response,
    routed_transport,
    token_payload,
)

HELIX_STREAMS_URL = f"{HELIX_BASE_URL}/streams"

TWITCH_USER_ID = "37402112"
TWITCH_STREAM_ID = "41375541868"
STARTED_AT = "2026-09-04T09:00:00Z"

__all__ = [
    "FAKE_APP_TOKEN",
    "HELIX_STREAMS_URL",
    "OAUTH_TOKEN_URL",
    "STARTED_AT",
    "TWITCH_STREAM_ID",
    "TWITCH_USER_ID",
    "RecordingTransport",
    "json_response",
    "routed_transport",
]


def twitch_stream(
    *,
    stream_id: str = TWITCH_STREAM_ID,
    user_id: str = TWITCH_USER_ID,
    title: str = "Ranked grind",
    game_id: str = "509658",
    game_name: str = "Just Chatting",
    viewer_count: int = 1200,
    started_at: str = STARTED_AT,
    language: str = "en",
    stream_type: str = "live",
    is_mature: bool = False,
) -> dict[str, Any]:
    """One entry as Twitch returns it, including fields ClipperStash ignores."""
    return {
        "id": stream_id,
        "user_id": user_id,
        "user_login": "shroud",
        "user_name": "shroud",
        "game_id": game_id,
        "game_name": game_name,
        "type": stream_type,
        "title": title,
        "tags": ["English"],
        "viewer_count": viewer_count,
        "started_at": started_at,
        "language": language,
        "thumbnail_url": "https://static-cdn.example/preview-{width}x{height}.jpg",
        "tag_ids": [],
        "is_mature": is_mature,
    }


def token_route() -> dict[str, Any]:
    return {
        OAUTH_TOKEN_URL: json_response(
            200, token_payload(access_token=FAKE_APP_TOKEN, refresh_token=None)
        )
    }


def live_transport(streams: list[dict[str, Any]] | None = None) -> RecordingTransport:
    """A transport answering the app-token request and one Get Streams call."""
    entries = [twitch_stream()] if streams is None else streams
    return routed_transport(
        {**token_route(), HELIX_STREAMS_URL: json_response(200, {"data": entries})}
    )


def offline_transport() -> RecordingTransport:
    """A successful Get Streams response with no stream: the broadcaster is offline."""
    return live_transport(streams=[])


def failing_transport(
    response: httpx.Response | Callable[..., httpx.Response],
) -> RecordingTransport:
    """A transport whose Get Streams call fails in some way."""
    return routed_transport({**token_route(), HELIX_STREAMS_URL: response})


@pytest.fixture
def streamer(db) -> Streamer:
    return Streamer.objects.create(
        platform=Platform.TWITCH,
        platform_user_id=TWITCH_USER_ID,
        username="shroud",
        display_name="shroud",
        channel_url="https://www.twitch.tv/shroud",
        profile_image_url="https://static-cdn.example/shroud-profile.png",
        broadcaster_type="partner",
        description="Professional gamer.",
    )


@pytest.fixture
def other_streamer(db) -> Streamer:
    return Streamer.objects.create(
        platform=Platform.TWITCH,
        platform_user_id="99",
        username="someoneelse",
        display_name="Someone Else",
        channel_url="https://www.twitch.tv/someoneelse",
    )


@pytest.fixture
def observe() -> Callable[[Streamer, RecordingTransport], Any]:
    """Run one observation against a mock transport."""

    def run(target: Streamer, transport: RecordingTransport) -> Any:
        return monitoring_services.observe_streamer(
            target, client=TwitchClient(transport=transport)
        )

    return run


@pytest.fixture
def patch_observation_client(monkeypatch) -> Callable[[RecordingTransport], None]:
    """Route the clients built beneath the API view through a mock transport."""

    def apply(transport: RecordingTransport) -> None:
        from apps.twitch import services as twitch_services

        monkeypatch.setattr(
            twitch_services,
            "TwitchClient",
            lambda *args, **kwargs: TwitchClient(transport=transport),
        )

    return apply
