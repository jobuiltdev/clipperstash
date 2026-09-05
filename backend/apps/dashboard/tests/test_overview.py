"""Tests for `GET /api/dashboard/overview/`."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse

from apps.clips.models import Clip
from apps.dashboard import state
from apps.moments.models import MomentCandidateStatus

from .conftest import NOW, make_candidate

pytestmark = pytest.mark.django_db

URL = reverse("dashboard:overview")


def test_empty_installation_answers_with_zeros_not_an_error(client):
    response = client.get(URL)

    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"]["streamers"]["total"] == 0
    assert payload["counts"]["sessions"]["total"] == 0
    assert payload["counts"]["moments"]["total"] == 0
    assert payload["live_sessions"] == []
    assert payload["recent_moments"] == []
    assert payload["recent_clips"] == []


def test_counts_describe_the_installation(client, populated):
    payload = client.get(URL).json()["counts"]

    assert payload["streamers"] == {"total": 2, "active": 2}
    assert payload["sessions"] == {"total": 3, "live": 2, "ended": 1}


def test_moment_counts_partition_every_candidate_exactly_once(client, populated):
    """Summary cards must add up, or an operator cannot trust any of them."""
    counts = client.get(URL).json()["counts"]["moments"]

    assert counts[state.NOT_REQUESTED] == 2  # detected + rejected
    assert counts[state.REQUESTED] == 1
    assert counts[state.REQUEST_UNKNOWN] == 1
    assert counts[state.CREATED] == 1
    assert counts[state.FAILED] == 1

    parts = sum(counts[name] for name in state.CLIP_STATES)
    assert parts == counts["total"] == 6


def test_an_unknown_request_is_not_counted_as_a_failure(client, populated):
    counts = client.get(URL).json()["counts"]["moments"]

    assert counts[state.FAILED] == 1
    assert counts[state.REQUEST_UNKNOWN] == 1


def test_live_sessions_are_listed_separately(client, populated):
    payload = client.get(URL).json()

    ids = [session["id"] for session in payload["live_sessions"]]
    assert populated.live_session.pk in ids
    assert populated.other_session.pk in ids
    assert populated.ended_session.pk not in ids


def test_sessions_carry_their_streamer_and_moment_tallies(client, populated):
    payload = client.get(URL).json()

    session = next(
        row for row in payload["recent_sessions"] if row["id"] == populated.live_session.pk
    )
    assert session["streamer"]["username"] == "shroud"
    assert session["status"] == "live"
    assert session["moment_count"] == 4
    assert session["clip_created_count"] == 1
    assert session["viewer_count"] == 4200
    assert session["category"] == "Just Chatting"


def test_recent_moments_are_newest_first_and_name_their_streamer(client, populated):
    rows = client.get(URL).json()["recent_moments"]

    assert [row["id"] for row in rows][0] == populated.detected.pk
    assert rows[0]["streamer"]["username"] == "shroud"
    assert rows[0]["session_id"] == populated.live_session.pk
    assert rows[0]["clip_state"] == state.NOT_REQUESTED


# -- recent clips: verified only ---------------------------------------------
#
# A local Clip row means ClipperStash *asked* Twitch for a clip. M6 writes that
# row before the external request precisely so it can represent a request whose
# outcome is unknown. So the existence of a row proves nothing about the clip,
# and this feed must be built from verification instead.


def recent_clip_ids(client) -> list[int]:
    return [row["id"] for row in client.get(URL).json()["recent_clips"]]


def make_verified(session, *, detected_at, clip_id: str, ready_at):
    """A moment whose clip Twitch was confirmed to have created."""
    candidate = make_candidate(
        session,
        detected_at=detected_at,
        status=MomentCandidateStatus.CLIP_CREATED,
    )
    Clip.objects.create(
        moment=candidate,
        twitch_clip_id=clip_id,
        twitch_url=f"https://clips.twitch.tv/{clip_id}",
        requested_at=detected_at,
        ready_at=ready_at,
    )
    return candidate


def test_a_verified_clip_appears_in_recent_clips(client, populated):
    rows = client.get(URL).json()["recent_clips"]

    assert [row["id"] for row in rows] == [populated.created.pk]
    assert rows[0]["clip_state"] == state.CREATED
    assert rows[0]["twitch_clip_id"] == "CreatedClipId"
    assert rows[0]["ready_at"] is not None


def test_an_accepted_but_unverified_request_is_excluded(client, populated):
    """Twitch acknowledged this one and nothing has confirmed the clip exists."""
    assert populated.requested.pk not in recent_clip_ids(client)


def test_a_request_of_unknown_outcome_is_excluded(client, populated):
    assert populated.unknown.pk not in recent_clip_ids(client)


def test_a_failed_request_is_excluded(client, populated):
    assert populated.failed.pk not in recent_clip_ids(client)


def test_a_clip_created_candidate_missing_its_confirmation_is_excluded(client, populated):
    """CLIP_CREATED alone is not enough; `ready_at` is what verification writes."""
    candidate = make_candidate(
        populated.live_session,
        detected_at=NOW - timedelta(minutes=6),
        status=MomentCandidateStatus.CLIP_CREATED,
    )
    Clip.objects.create(
        moment=candidate,
        twitch_clip_id="UnconfirmedClipId",
        requested_at=NOW - timedelta(minutes=6),
    )

    assert candidate.pk not in recent_clip_ids(client)


def test_verified_clips_are_ordered_by_when_they_were_confirmed(client, populated):
    older = make_verified(
        populated.live_session,
        detected_at=NOW - timedelta(minutes=20),
        clip_id="OlderVerifiedClip",
        ready_at=NOW - timedelta(minutes=19),
    )
    newer = make_verified(
        populated.live_session,
        detected_at=NOW - timedelta(minutes=10),
        clip_id="NewerVerifiedClip",
        ready_at=NOW - timedelta(seconds=30),
    )

    assert recent_clip_ids(client) == [newer.pk, populated.created.pk, older.pk]


def test_a_later_request_cannot_displace_a_verified_clip(client, populated):
    """The fixture's unverified request was made after the verified clip's.

    Ordering on request time would put it first; ordering on confirmation time
    excludes it entirely, which is the point.
    """
    assert populated.requested.clip.requested_at > populated.created.clip.requested_at

    assert recent_clip_ids(client) == [populated.created.pk]


def test_the_recent_feed_is_bounded(client, populated):
    payload = client.get(URL, {"limit": 2}).json()

    assert len(payload["recent_moments"]) == 2


def test_an_absurd_limit_is_clamped_rather_than_honoured(client, populated):
    response = client.get(URL, {"limit": 10_000_000})

    assert response.status_code == 200
    assert len(response.json()["recent_moments"]) <= 200


def test_a_nonsense_limit_is_a_clear_error(client, populated):
    response = client.get(URL, {"limit": "lots"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_query_parameter"


def test_the_overview_costs_a_fixed_number_of_queries(client, populated, django_assert_num_queries):
    """Constant cost, so the page does not slow down as moments accumulate."""
    with django_assert_num_queries(7):
        client.get(URL)

    # Every feed on this page selects the rows its serializer touches, so more
    # data must not turn into more queries.
    for index in range(20):
        make_verified(
            populated.live_session,
            detected_at=NOW - timedelta(hours=2, minutes=index),
            clip_id=f"BulkVerifiedClip{index}",
            ready_at=NOW - timedelta(hours=1, minutes=index),
        )

    with django_assert_num_queries(7):
        client.get(URL)
