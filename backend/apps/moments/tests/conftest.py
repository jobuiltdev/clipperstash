"""Fixtures for the moments service tests.

Nothing here contacts Twitch: the detector reads only chat already persisted by
Milestone 4, so these tests need no network of any kind.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from apps.monitoring.models import ChatMessage, StreamSession, StreamSessionStatus
from apps.streamers.models import Platform, Streamer

# A fixed evaluation instant, so every window assertion is exact.
EVALUATION_TIME = datetime(2026, 9, 4, 18, 0, 0, tzinfo=UTC)


@pytest.fixture
def streamer(db) -> Streamer:
    return Streamer.objects.create(
        platform=Platform.TWITCH,
        platform_user_id="37402112",
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
        started_at=EVALUATION_TIME - timedelta(hours=1),
        last_observed_at=EVALUATION_TIME,
        status=status,
    )


@pytest.fixture
def session(db, streamer) -> StreamSession:
    return make_session(streamer, stream_id="stream-1", status=StreamSessionStatus.LIVE)


@pytest.fixture
def other_session(db, other_streamer) -> StreamSession:
    return make_session(other_streamer, stream_id="stream-2", status=StreamSessionStatus.LIVE)


@pytest.fixture
def ended_session(db, streamer) -> StreamSession:
    session = make_session(streamer, stream_id="stream-ended", status=StreamSessionStatus.ENDED)
    StreamSession.objects.filter(pk=session.pk).update(ended_at=EVALUATION_TIME)
    session.refresh_from_db()
    return session


@pytest.fixture
def add_messages(db) -> Callable[..., list[ChatMessage]]:
    """Persist chat spread across a span of seconds before the evaluation time."""
    counter = {"value": 0}

    def add(
        session: StreamSession,
        count: int,
        *,
        unique: int,
        seconds_before_start: float,
        seconds_before_end: float,
        text: str = "hello",
        emotes: int = 0,
        at: datetime = EVALUATION_TIME,
    ) -> list[ChatMessage]:
        if count == 0:
            return []
        step = (seconds_before_start - seconds_before_end) / count
        created = []
        for index in range(count):
            counter["value"] += 1
            unique_id = counter["value"]
            offset = seconds_before_start - step * index - step / 2
            created.append(
                ChatMessage.objects.create(
                    session=session,
                    twitch_event_message_id=f"event-{unique_id}",
                    twitch_message_id=f"msg-{unique_id}",
                    chatter_user_id_hash=f"hash-{index % unique}",
                    text=text,
                    timestamp=at - timedelta(seconds=offset),
                    emote_count=emotes,
                )
            )
        return created

    return add


@pytest.fixture
def quiet_chat(add_messages) -> Callable[[StreamSession], None]:
    """Enough baseline traffic to be a real comparison, and a calm current window."""

    def build(session: StreamSession) -> None:
        add_messages(session, 30, unique=12, seconds_before_start=70, seconds_before_end=10)
        add_messages(session, 3, unique=3, seconds_before_start=10, seconds_before_end=0)

    return build


@pytest.fixture
def qualifying_chat(add_messages) -> Callable[..., None]:
    """A broad, emote-heavy reaction burst that clears the threshold."""

    def build(session: StreamSession, *, at: datetime = EVALUATION_TIME) -> None:
        add_messages(session, 30, unique=12, seconds_before_start=70, seconds_before_end=10, at=at)
        add_messages(
            session,
            30,
            unique=25,
            seconds_before_start=10,
            seconds_before_end=0,
            text="LMAO no way clip it",
            emotes=2,
            at=at,
        )

    return build
