"""Tests for the StreamSession lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.monitoring.exceptions import StreamStateUnavailableError
from apps.monitoring.models import StreamSession, StreamSessionStatus
from apps.monitoring.services import LIVE, OFFLINE, current_session
from apps.twitch.tests.conftest import json_response

from .conftest import (
    STARTED_AT,
    TWITCH_STREAM_ID,
    failing_transport,
    live_transport,
    offline_transport,
    twitch_stream,
)

pytestmark = pytest.mark.django_db

STARTED_AT_UTC = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)


# -- first live observation --------------------------------------------------


def test_first_live_observation_creates_a_session(observe, streamer):
    observation = observe(streamer, live_transport())

    assert observation.status == LIVE
    assert StreamSession.objects.count() == 1

    session = observation.session
    assert session.streamer == streamer
    assert session.platform_stream_id == TWITCH_STREAM_ID
    assert session.started_at == STARTED_AT_UTC
    assert session.title == "Ranked grind"
    assert session.category_id == "509658"
    assert session.category_name == "Just Chatting"
    assert session.language == "en"
    assert session.is_mature is False
    assert session.last_viewer_count == 1200
    assert session.status == StreamSessionStatus.LIVE
    assert session.ended_at is None
    assert session.last_observed_at is not None
    assert session.last_observed_at.tzinfo is not None


def test_started_at_comes_from_twitch_not_the_local_clock(observe, streamer):
    session = observe(streamer, live_transport()).session

    assert session.started_at == STARTED_AT_UTC
    assert session.started_at < session.last_observed_at


def test_observation_uses_the_stored_platform_user_id(observe, streamer):
    """Live checks never re-resolve the channel through Get Users."""
    from .conftest import HELIX_STREAMS_URL

    transport = live_transport()
    observe(streamer, transport)

    urls = {str(request.url).split("?")[0] for request in transport.requests}
    assert HELIX_STREAMS_URL in urls
    assert "https://api.twitch.tv/helix/users" not in urls
    assert transport.request_for(HELIX_STREAMS_URL).url.params["user_id"] == (
        streamer.platform_user_id
    )


# -- repeated live observation -----------------------------------------------


def test_repeated_live_observation_reuses_the_session(observe, streamer):
    first = observe(streamer, live_transport()).session
    second = observe(streamer, live_transport()).session

    assert StreamSession.objects.count() == 1
    assert first.pk == second.pk


def test_repeated_live_observation_refreshes_mutable_metadata(observe, streamer):
    original = observe(streamer, live_transport()).session
    original_started_at = original.started_at

    updated = observe(
        streamer,
        live_transport(
            [
                twitch_stream(
                    title="Now playing chess",
                    game_id="743",
                    game_name="Chess",
                    viewer_count=4321,
                )
            ]
        ),
    ).session

    assert updated.pk == original.pk
    assert updated.title == "Now playing chess"
    assert updated.category_id == "743"
    assert updated.category_name == "Chess"
    assert updated.last_viewer_count == 4321
    assert updated.started_at == original_started_at, "Twitch's start time is never rewritten"
    assert updated.ended_at is None
    assert updated.status == StreamSessionStatus.LIVE


def test_repeated_live_observation_advances_last_observed_at(observe, streamer):
    first = observe(streamer, live_transport()).session
    first_observed_at = first.last_observed_at

    second = observe(streamer, live_transport()).session

    assert second.last_observed_at > first_observed_at


# -- new broadcast -----------------------------------------------------------


def test_a_new_stream_id_closes_the_previous_session(observe, streamer):
    """Covers a missed offline transition between two broadcasts."""
    first = observe(streamer, live_transport()).session

    second = observe(
        streamer,
        live_transport([twitch_stream(stream_id="999", started_at="2026-09-05T09:00:00Z")]),
    ).session

    first.refresh_from_db()
    assert first.status == StreamSessionStatus.ENDED
    assert first.ended_at is not None
    assert first.started_at == STARTED_AT_UTC, "history is preserved"

    assert second.pk != first.pk
    assert second.status == StreamSessionStatus.LIVE
    assert second.platform_stream_id == "999"
    assert second.ended_at is None

    assert StreamSession.objects.count() == 2
    assert StreamSession.objects.filter(status=StreamSessionStatus.LIVE).count() == 1


def test_only_one_live_session_survives_a_sequence_of_broadcasts(observe, streamer):
    observe(streamer, live_transport())
    observe(streamer, live_transport([twitch_stream(stream_id="2")]))
    observe(streamer, live_transport([twitch_stream(stream_id="3")]))

    assert StreamSession.objects.count() == 3
    assert (
        StreamSession.objects.filter(streamer=streamer, status=StreamSessionStatus.LIVE).count()
        == 1
    )
    assert current_session(streamer).platform_stream_id == "3"


# -- offline -----------------------------------------------------------------


def test_offline_observation_closes_the_live_session(observe, streamer):
    session = observe(streamer, live_transport()).session

    observation = observe(streamer, offline_transport())

    assert observation.status == OFFLINE
    assert observation.session is None

    session.refresh_from_db()
    assert session.status == StreamSessionStatus.ENDED
    assert session.ended_at is not None
    assert session.started_at == STARTED_AT_UTC
    assert session.title == "Ranked grind", "historical metadata is preserved"
    assert session.last_viewer_count == 1200


def test_repeated_offline_observation_is_idempotent(observe, streamer):
    session = observe(streamer, live_transport()).session
    observe(streamer, offline_transport())

    session.refresh_from_db()
    first_ended_at = session.ended_at

    observe(streamer, offline_transport())
    observe(streamer, offline_transport())

    session.refresh_from_db()
    assert session.ended_at == first_ended_at, "an ended session is not re-ended"
    assert StreamSession.objects.count() == 1


def test_offline_with_no_session_creates_nothing(observe, streamer):
    observation = observe(streamer, offline_transport())

    assert observation.status == OFFLINE
    assert observation.session is None
    assert StreamSession.objects.count() == 0


def test_offline_only_closes_the_observed_streamer(observe, streamer, other_streamer):
    mine = observe(streamer, live_transport()).session
    theirs = observe(
        other_streamer,
        live_transport([twitch_stream(stream_id="888", user_id=other_streamer.platform_user_id)]),
    ).session

    observe(streamer, offline_transport())

    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert mine.status == StreamSessionStatus.ENDED
    assert theirs.status == StreamSessionStatus.LIVE


def test_a_stream_can_come_back_under_the_same_id(observe, streamer):
    """A mis-observed offline state is corrected rather than duplicated."""
    session = observe(streamer, live_transport()).session
    observe(streamer, offline_transport())

    resumed = observe(streamer, live_transport()).session

    assert resumed.pk == session.pk
    assert resumed.status == StreamSessionStatus.LIVE
    assert resumed.ended_at is None
    assert StreamSession.objects.count() == 1


# -- failure semantics -------------------------------------------------------


def transport_failures():
    def explode(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    return [
        pytest.param(failing_transport(json_response(500, {"message": "boom"})), id="server-error"),
        pytest.param(failing_transport(json_response(503, {"message": "down"})), id="unavailable"),
        pytest.param(failing_transport(explode), id="timeout"),
        pytest.param(failing_transport(json_response(200, {"data": "nonsense"})), id="malformed"),
        pytest.param(
            failing_transport(json_response(401, {"message": "Invalid OAuth token"})),
            id="auth-failure",
        ),
    ]


@pytest.mark.parametrize("transport", transport_failures())
def test_a_failed_observation_raises_rather_than_reporting_offline(observe, streamer, transport):
    with pytest.raises(StreamStateUnavailableError):
        observe(streamer, transport)


@pytest.mark.parametrize("transport", transport_failures())
def test_a_failed_observation_leaves_an_active_session_untouched(observe, streamer, transport):
    session = observe(streamer, live_transport()).session
    before = StreamSession.objects.get(pk=session.pk)

    with pytest.raises(StreamStateUnavailableError):
        observe(streamer, transport)

    after = StreamSession.objects.get(pk=session.pk)
    assert after.status == StreamSessionStatus.LIVE
    assert after.ended_at is None
    assert after.last_observed_at == before.last_observed_at
    assert after.last_viewer_count == before.last_viewer_count
    assert after.updated_at == before.updated_at


@pytest.mark.parametrize("transport", transport_failures())
def test_a_failed_observation_creates_nothing(observe, streamer, transport):
    with pytest.raises(StreamStateUnavailableError):
        observe(streamer, transport)

    assert StreamSession.objects.count() == 0


def test_unavailable_error_carries_no_twitch_detail(observe, streamer):
    transport = failing_transport(json_response(500, {"message": "Internal Server Error"}))

    with pytest.raises(StreamStateUnavailableError) as exc_info:
        observe(streamer, transport)

    assert "Internal Server Error" not in str(exc_info.value)


# -- database invariants -----------------------------------------------------


def make_session(streamer, **overrides) -> StreamSession:
    values = {
        "streamer": streamer,
        "platform_stream_id": "s-1",
        "started_at": timezone.now() - timedelta(hours=1),
        "last_observed_at": timezone.now(),
        "status": StreamSessionStatus.LIVE,
    }
    values.update(overrides)
    return StreamSession.objects.create(**values)


def test_duplicate_platform_stream_id_is_rejected(streamer, other_streamer):
    make_session(streamer, platform_stream_id="dup")

    with pytest.raises(IntegrityError), transaction.atomic():
        make_session(other_streamer, platform_stream_id="dup", status=StreamSessionStatus.ENDED)


def test_a_second_live_session_for_one_streamer_is_rejected(streamer):
    make_session(streamer, platform_stream_id="a")

    with pytest.raises(IntegrityError), transaction.atomic():
        make_session(streamer, platform_stream_id="b")


def test_many_ended_sessions_for_one_streamer_are_allowed(streamer):
    make_session(streamer, platform_stream_id="a", status=StreamSessionStatus.ENDED)
    make_session(streamer, platform_stream_id="b", status=StreamSessionStatus.ENDED)
    make_session(streamer, platform_stream_id="c", status=StreamSessionStatus.LIVE)

    assert StreamSession.objects.filter(streamer=streamer).count() == 3
    assert (
        StreamSession.objects.filter(streamer=streamer, status=StreamSessionStatus.LIVE).count()
        == 1
    )


def test_two_streamers_may_each_be_live(streamer, other_streamer):
    make_session(streamer, platform_stream_id="a")
    make_session(other_streamer, platform_stream_id="b")

    assert StreamSession.objects.filter(status=StreamSessionStatus.LIVE).count() == 2


def test_the_live_constraint_blocks_a_racing_second_session(observe, streamer):
    """The database, not application code, is the backstop against a race.

    A second observation that had already decided to open a session, while
    another had just opened one for a different broadcast, is refused outright.
    """
    observe(streamer, live_transport())

    with pytest.raises(IntegrityError), transaction.atomic():
        make_session(streamer, platform_stream_id="racing")


def test_sequential_observations_never_leave_two_live_sessions(observe, streamer):
    for stream_id in ("a", "b", "c", "a", "d"):
        try:
            observe(streamer, live_transport([twitch_stream(stream_id=stream_id)]))
        except IntegrityError:  # pragma: no cover - would be a lifecycle bug
            pytest.fail("the lifecycle must keep the live invariant on its own")
        assert (
            StreamSession.objects.filter(streamer=streamer, status=StreamSessionStatus.LIVE).count()
            == 1
        )


def test_session_string_representations_are_readable(streamer):
    session = make_session(streamer, platform_stream_id="abc")

    assert "shroud" in str(session)
    assert "abc" in repr(session)
    assert session.is_live is True


def test_started_at_string_fixture_matches_the_parsed_value():
    """Guards the fixture itself, so lifecycle assertions stay meaningful."""
    assert STARTED_AT == "2026-09-04T09:00:00Z"
