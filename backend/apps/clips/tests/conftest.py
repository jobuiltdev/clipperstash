"""Fixtures for clip creation tests.

Twitch is never contacted: every HTTP exchange is served in process by the same
`httpx.MockTransport` helpers the rest of the suite uses, and the verification
loop's clock and sleeper are injected so polling completes instantly.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.moments.models import MomentCandidate, MomentCandidateStatus
from apps.monitoring.models import StreamSession, StreamSessionStatus
from apps.streamers.models import Platform, Streamer
from apps.twitch.client import HELIX_BASE_URL, OAUTH_TOKEN_URL, TwitchClient
from apps.twitch.models import TwitchConnection
from apps.twitch.tests.conftest import (
    FAKE_ACCESS_TOKEN,
    FAKE_REFRESH_TOKEN,
    RecordingTransport,
    json_response,
    routed_transport,
    token_payload,
)

HELIX_CLIPS_URL = f"{HELIX_BASE_URL}/clips"

BROADCASTER_ID = "37402112"
TWITCH_CLIP_ID = "AwkwardHelplessSalamanderSwiftRage"
CLIP_URL = "https://clips.twitch.tv/AwkwardHelplessSalamanderSwiftRage"
CLIP_CREATED_AT = "2026-09-05T12:00:00Z"

NOW = datetime(2026, 9, 5, 12, 0, 10, tzinfo=UTC)


# -- domain fixtures ----------------------------------------------------------


@pytest.fixture
def streamer(db) -> Streamer:
    return Streamer.objects.create(
        platform=Platform.TWITCH,
        platform_user_id=BROADCASTER_ID,
        username="shroud",
        display_name="shroud",
        channel_url="https://www.twitch.tv/shroud",
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


def make_session(streamer: Streamer, *, stream_id: str, status: str) -> StreamSession:
    return StreamSession.objects.create(
        streamer=streamer,
        platform_stream_id=stream_id,
        started_at=NOW - timedelta(hours=1),
        last_observed_at=NOW,
        status=status,
    )


@pytest.fixture
def session(db, streamer) -> StreamSession:
    return make_session(streamer, stream_id="stream-1", status=StreamSessionStatus.LIVE)


@pytest.fixture
def ended_session(db, streamer) -> StreamSession:
    session = make_session(streamer, stream_id="stream-ended", status=StreamSessionStatus.ENDED)
    StreamSession.objects.filter(pk=session.pk).update(ended_at=NOW)
    session.refresh_from_db()
    return session


def make_candidate(
    session: StreamSession,
    *,
    detected_at: datetime = NOW,
    status: str = MomentCandidateStatus.DETECTED,
    total_score: float = 88.0,
) -> MomentCandidate:
    return MomentCandidate.objects.create(
        session=session,
        detected_at=detected_at,
        current_window_start=detected_at - timedelta(seconds=10),
        current_window_end=detected_at,
        baseline_window_start=detected_at - timedelta(seconds=70),
        baseline_window_end=detected_at - timedelta(seconds=10),
        current_message_count=30,
        baseline_message_count=30,
        current_unique_chatters=25,
        baseline_unique_chatters=12,
        current_emote_count=60,
        baseline_emote_count=0,
        current_reaction_count=30,
        baseline_reaction_count=0,
        velocity_ratio=6.0,
        velocity_score=1.0,
        reaction_score=1.0,
        emote_score=1.0,
        diversity_score=0.9,
        absolute_activity_score=1.0,
        total_score=total_score,
        status=status,
    )


@pytest.fixture
def candidate(db, session) -> MomentCandidate:
    """A fresh, detected candidate on a live session."""
    return make_candidate(session)


@pytest.fixture
def connection(db) -> TwitchConnection:
    """A connected account carrying both required scopes."""
    return TwitchConnection.objects.create(
        twitch_user_id="123456",
        login="example",
        display_name="Example",
        access_token=FAKE_ACCESS_TOKEN,
        refresh_token=FAKE_REFRESH_TOKEN,
        token_expires_at=timezone.now() + timedelta(hours=4),
        scopes=["clips:edit", "user:read:chat"],
    )


# -- Twitch responses ---------------------------------------------------------


def accepted_payload(clip_id: str = TWITCH_CLIP_ID) -> dict[str, Any]:
    """Twitch's 202 body, including the edit_url ClipperStash discards."""
    return {
        "data": [
            {
                "id": clip_id,
                "edit_url": f"https://clips.twitch.tv/{clip_id}/edit",
            }
        ]
    }


def clip_payload(
    *,
    clip_id: str = TWITCH_CLIP_ID,
    broadcaster_id: str = BROADCASTER_ID,
    url: str = CLIP_URL,
    title: str = "insane play",
    duration: float = 28.5,
) -> dict[str, Any]:
    """One `GET /helix/clips` entry, including fields ClipperStash drops."""
    return {
        "data": [
            {
                "id": clip_id,
                "url": url,
                "embed_url": f"https://clips.twitch.tv/embed?clip={clip_id}",
                "broadcaster_id": broadcaster_id,
                "broadcaster_name": "shroud",
                "creator_id": "123456",
                "creator_name": "Example",
                "video_id": "1234567890",
                "game_id": "509658",
                "language": "en",
                "title": title,
                "view_count": 3,
                "created_at": CLIP_CREATED_AT,
                "thumbnail_url": f"https://clips-media.example/{clip_id}-preview.jpg",
                "duration": duration,
                "vod_offset": 1200,
                "is_featured": False,
            }
        ]
    }


EMPTY_CLIPS: dict[str, Any] = {"data": []}


def clip_transport(
    *,
    create: Any = None,
    lookups: list[Any] | None = None,
) -> RecordingTransport:
    """A transport for the POST and however many GETs a test needs.

    Both share the `/helix/clips` path, so the route dispatches on method.
    """
    create_response = create if create is not None else json_response(202, accepted_payload())
    remaining = list(lookups) if lookups is not None else [json_response(200, clip_payload())]

    def handler(request):
        if request.method == "POST":
            if callable(create_response):
                return create_response(request)
            return create_response
        if not remaining:
            raise AssertionError("More clip lookups were made than the test scripted.")
        nxt = remaining.pop(0)
        if callable(nxt):
            return nxt(request)
        return nxt

    return routed_transport(
        {
            HELIX_CLIPS_URL: handler,
            # Present so a test can assert it is never called: clip creation
            # must not mint an app token.
            OAUTH_TOKEN_URL: json_response(200, token_payload()),
        }
    )


@pytest.fixture
def make_client() -> Callable[[RecordingTransport], TwitchClient]:
    def factory(transport: RecordingTransport) -> TwitchClient:
        return TwitchClient(transport=transport)

    return factory


# -- injected time ------------------------------------------------------------


class FakeClock:
    """A monotonic clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.value = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.value += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
