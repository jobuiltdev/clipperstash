"""Fixtures for the dashboard read tests.

Everything here builds rows directly. The dashboard is a read surface, so its
tests never run the pipeline that would normally produce these rows — and never
contact Twitch, which the suite-wide guards make impossible anyway.

The `populated` fixture deliberately contains one moment in every display clip
state, including the indeterminate one, because the states the dashboard has to
tell apart are exactly the ones that are easy to conflate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from apps.clips.models import Clip, ClipFailureCode
from apps.moments.models import MomentCandidate, MomentCandidateStatus
from apps.monitoring.models import ChatMessage, StreamSession, StreamSessionStatus
from apps.streamers.models import Platform, Streamer

NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)

# A recognizable secret-shaped string. Chat text must never reach a response,
# so the fixture chat says something a test can assert the absence of.
CHAT_TEXT = "PRIVATE-CHAT-TEXT-must-not-appear-4c19bd"
CHATTER_HASH = "0f9a" * 16


def make_streamer(username: str, platform_user_id: str) -> Streamer:
    return Streamer.objects.create(
        platform=Platform.TWITCH,
        platform_user_id=platform_user_id,
        username=username,
        display_name=username.title(),
        channel_url=f"https://www.twitch.tv/{username}",
        profile_image_url=f"https://static-cdn.example/{username}.png",
    )


def make_session(
    streamer: Streamer,
    *,
    stream_id: str,
    status: str = StreamSessionStatus.LIVE,
    started_at: datetime = NOW - timedelta(hours=2),
    title: str = "ranked grind",
) -> StreamSession:
    session = StreamSession.objects.create(
        streamer=streamer,
        platform_stream_id=stream_id,
        started_at=started_at,
        last_observed_at=NOW,
        title=title,
        category_id="509658",
        category_name="Just Chatting",
        language="en",
        is_mature=False,
        last_viewer_count=4200,
        status=status,
    )
    if status == StreamSessionStatus.ENDED:
        StreamSession.objects.filter(pk=session.pk).update(ended_at=NOW)
        session.refresh_from_db()
    return session


def make_candidate(
    session: StreamSession,
    *,
    detected_at: datetime = NOW,
    status: str = MomentCandidateStatus.DETECTED,
    total_score: float = 84.0,
) -> MomentCandidate:
    return MomentCandidate.objects.create(
        session=session,
        detected_at=detected_at,
        current_window_start=detected_at - timedelta(seconds=10),
        current_window_end=detected_at,
        baseline_window_start=detected_at - timedelta(seconds=70),
        baseline_window_end=detected_at - timedelta(seconds=10),
        current_message_count=42,
        baseline_message_count=12,
        current_unique_chatters=19,
        baseline_unique_chatters=7,
        current_emote_count=55,
        baseline_emote_count=4,
        current_reaction_count=21,
        baseline_reaction_count=1,
        velocity_ratio=3.5,
        velocity_score=0.87,
        reaction_score=0.75,
        emote_score=0.66,
        diversity_score=0.9,
        absolute_activity_score=1.0,
        total_score=total_score,
        status=status,
    )


def make_chat_message(session: StreamSession, index: int) -> ChatMessage:
    return ChatMessage.objects.create(
        session=session,
        twitch_event_message_id=f"event-{session.pk}-{index}",
        twitch_message_id=f"message-{session.pk}-{index}",
        chatter_user_id_hash=CHATTER_HASH,
        text=CHAT_TEXT,
        timestamp=NOW - timedelta(seconds=index),
        emote_count=2,
    )


@dataclass
class Fixtureset:
    """Named handles on the rows the dashboard tests assert against."""

    streamer: Streamer
    other_streamer: Streamer
    live_session: StreamSession
    ended_session: StreamSession
    other_session: StreamSession
    detected: MomentCandidate
    requested: MomentCandidate
    unknown: MomentCandidate
    created: MomentCandidate
    failed: MomentCandidate
    rejected: MomentCandidate
    created_clip: Clip


@pytest.fixture
def streamer(db) -> Streamer:
    return make_streamer("shroud", "37402112")


@pytest.fixture
def live_session(db, streamer) -> StreamSession:
    return make_session(streamer, stream_id="stream-live")


@pytest.fixture
def populated(db) -> Fixtureset:
    """One installation's worth of state, with every clip display state present."""
    streamer = make_streamer("shroud", "37402112")
    other_streamer = make_streamer("someoneelse", "99")

    live_session = make_session(streamer, stream_id="stream-live")
    ended_session = make_session(
        streamer,
        stream_id="stream-ended",
        status=StreamSessionStatus.ENDED,
        started_at=NOW - timedelta(days=1),
        title="last night",
    )
    other_session = make_session(other_streamer, stream_id="stream-other")

    # DETECTED: nothing was ever asked of Twitch.
    detected = make_candidate(live_session, detected_at=NOW - timedelta(minutes=1))

    # CLIP_REQUESTED with an id: a request Twitch acknowledged, not yet verified.
    requested = make_candidate(
        live_session,
        detected_at=NOW - timedelta(minutes=2),
        status=MomentCandidateStatus.CLIP_REQUESTED,
    )
    Clip.objects.create(
        moment=requested,
        twitch_clip_id="RequestedClipId",
        requested_at=NOW - timedelta(minutes=2),
    )

    # CLIP_REQUESTED without an id: the outcome was never learned. This is the
    # row the dashboard must not report as a failure.
    unknown = make_candidate(
        live_session,
        detected_at=NOW - timedelta(minutes=3),
        status=MomentCandidateStatus.CLIP_REQUESTED,
    )
    Clip.objects.create(
        moment=unknown,
        requested_at=NOW - timedelta(minutes=3),
        failure_code=ClipFailureCode.REQUEST_STATE_UNKNOWN,
        failure_detail="The clip request's outcome is unknown.",
    )

    # CLIP_CREATED: verified against Twitch.
    created = make_candidate(
        live_session,
        detected_at=NOW - timedelta(minutes=4),
        status=MomentCandidateStatus.CLIP_CREATED,
        total_score=91.0,
    )
    created_clip = Clip.objects.create(
        moment=created,
        twitch_clip_id="CreatedClipId",
        twitch_url="https://clips.twitch.tv/CreatedClipId",
        title="insane play",
        duration=28.5,
        thumbnail_url="https://clips-media.example/CreatedClipId-preview.jpg",
        twitch_created_at=NOW - timedelta(minutes=4),
        requested_at=NOW - timedelta(minutes=4),
        ready_at=NOW - timedelta(minutes=3, seconds=50),
    )

    # FAILED: Twitch answered, definitively.
    failed = make_candidate(
        ended_session,
        detected_at=NOW - timedelta(days=1),
        status=MomentCandidateStatus.FAILED,
    )
    Clip.objects.create(
        moment=failed,
        requested_at=NOW - timedelta(days=1),
        failure_code=ClipFailureCode.TWITCH_CREATE_REJECTED,
        failure_detail="Twitch did not accept the clip request.",
    )

    # REJECTED: a candidate ruled out, never clipped.
    rejected = make_candidate(
        ended_session,
        detected_at=NOW - timedelta(days=1, minutes=5),
        status=MomentCandidateStatus.REJECTED,
        total_score=71.0,
    )

    for index in range(3):
        make_chat_message(live_session, index)

    return Fixtureset(
        streamer=streamer,
        other_streamer=other_streamer,
        live_session=live_session,
        ended_session=ended_session,
        other_session=other_session,
        detected=detected,
        requested=requested,
        unknown=unknown,
        created=created,
        failed=failed,
        rejected=rejected,
        created_clip=created_clip,
    )
