"""Tests for the moment list and moment detail endpoints."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.dashboard import state
from apps.moments.detector import DEFAULT_CONFIG

from .conftest import make_candidate

pytestmark = pytest.mark.django_db


def moments_url(session_id: int) -> str:
    return reverse("dashboard:session-moments", args=[session_id])


def detail_url(moment_id: int) -> str:
    return reverse("dashboard:moment-detail", args=[moment_id])


# -- the list ----------------------------------------------------------------


def test_moments_belong_to_their_session_only(client, populated):
    page = client.get(moments_url(populated.live_session.pk)).json()["moments"]

    ids = {row["id"] for row in page["results"]}
    assert ids == {
        populated.detected.pk,
        populated.requested.pk,
        populated.unknown.pk,
        populated.created.pk,
    }
    assert populated.failed.pk not in ids


def test_moments_are_newest_first(client, populated):
    page = client.get(moments_url(populated.live_session.pk)).json()["moments"]

    assert [row["id"] for row in page["results"]] == [
        populated.detected.pk,
        populated.requested.pk,
        populated.unknown.pk,
        populated.created.pk,
    ]


def test_each_row_carries_its_derived_clip_state(client, populated):
    page = client.get(moments_url(populated.live_session.pk)).json()["moments"]
    states = {row["id"]: row["clip_state"] for row in page["results"]}

    assert states[populated.detected.pk] == state.NOT_REQUESTED
    assert states[populated.requested.pk] == state.REQUESTED
    assert states[populated.unknown.pk] == state.REQUEST_UNKNOWN
    assert states[populated.created.pk] == state.CREATED


def test_a_created_row_links_to_the_clip_twitch_returned(client, populated):
    page = client.get(moments_url(populated.live_session.pk)).json()["moments"]
    row = next(r for r in page["results"] if r["id"] == populated.created.pk)

    assert row["twitch_clip_id"] == "CreatedClipId"
    assert row["twitch_url"] == "https://clips.twitch.tv/CreatedClipId"
    assert row["ready_at"] is not None


def test_rows_without_a_clip_report_empty_clip_fields(client, populated):
    page = client.get(moments_url(populated.live_session.pk)).json()["moments"]
    row = next(r for r in page["results"] if r["id"] == populated.detected.pk)

    assert row["clip_id"] is None
    assert row["twitch_clip_id"] is None
    assert row["twitch_url"] == ""
    assert row["requested_at"] is None


def test_rows_carry_the_full_score_breakdown(client, populated):
    """A list of bare totals cannot be calibrated against."""
    page = client.get(moments_url(populated.live_session.pk)).json()["moments"]
    row = page["results"][0]

    for component in (
        "velocity_score",
        "reaction_score",
        "emote_score",
        "diversity_score",
        "absolute_activity_score",
        "velocity_ratio",
        "current_message_count",
        "baseline_message_count",
        "current_unique_chatter_count",
    ):
        assert component in row


def test_moments_can_be_filtered_by_clip_state(client, populated):
    page = client.get(
        moments_url(populated.live_session.pk), {"clip_state": state.REQUEST_UNKNOWN}
    ).json()["moments"]

    assert [row["id"] for row in page["results"]] == [populated.unknown.pk]
    assert page["count"] == 1


def test_a_filtered_list_agrees_with_the_count_that_led_to_it(client, populated):
    detail = client.get(reverse("dashboard:session-detail", args=[populated.live_session.pk]))
    counts = detail.json()["session"]["counts"]

    for name in state.CLIP_STATES:
        page = client.get(moments_url(populated.live_session.pk), {"clip_state": name}).json()
        assert page["moments"]["count"] == counts[name]


def test_an_unknown_clip_state_filter_is_refused(client, populated):
    response = client.get(moments_url(populated.live_session.pk), {"clip_state": "pending"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_query_parameter"


def test_a_session_with_no_moments_gets_an_empty_page(client, populated):
    page = client.get(moments_url(populated.other_session.pk)).json()["moments"]

    assert page["results"] == []
    assert page["count"] == 0


def test_an_unknown_session_is_a_named_404(client, db):
    response = client.get(moments_url(9090))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


def test_listing_moments_costs_the_same_for_one_row_as_for_many(
    client, populated, django_assert_num_queries
):
    """The clip state of every row must not cost a query per row."""
    with django_assert_num_queries(3):
        client.get(moments_url(populated.live_session.pk))

    for index in range(25):
        make_candidate(populated.live_session, total_score=70.0 + index)

    with django_assert_num_queries(3):
        client.get(moments_url(populated.live_session.pk))


# -- the detail --------------------------------------------------------------


def test_moment_detail_explains_the_score(client, populated):
    moment = client.get(detail_url(populated.created.pk)).json()["moment"]

    assert moment["total_score"] == 91.0
    assert moment["threshold"] == DEFAULT_CONFIG.candidate_threshold
    assert moment["windows"]["current_start"] is not None
    assert moment["windows"]["baseline_end"] is not None
    assert moment["streamer"]["username"] == "shroud"
    assert moment["session_id"] == populated.live_session.pk


def test_moment_detail_includes_the_clip_when_there_is_one(client, populated):
    moment = client.get(detail_url(populated.created.pk)).json()["moment"]

    assert moment["clip"]["twitch_clip_id"] == "CreatedClipId"
    assert moment["clip"]["duration"] == 28.5
    assert moment["clip_state"] == state.CREATED


def test_moment_detail_reports_no_clip_as_null(client, populated):
    moment = client.get(detail_url(populated.detected.pk)).json()["moment"]

    assert moment["clip"] is None
    assert moment["clip_state"] == state.NOT_REQUESTED


def test_a_failed_moment_explains_why_in_controlled_words(client, populated):
    moment = client.get(detail_url(populated.failed.pk)).json()["moment"]

    assert moment["clip_state"] == state.FAILED
    assert moment["failure_code"] == "twitch_create_rejected"
    assert moment["failure_detail"] == "Twitch did not accept the clip request."


def test_an_unknown_request_is_shown_as_unknown_not_failed(client, populated):
    moment = client.get(detail_url(populated.unknown.pk)).json()["moment"]

    assert moment["clip_state"] == state.REQUEST_UNKNOWN
    assert moment["twitch_clip_id"] is None
    assert moment["failure_code"] == "request_state_unknown"


def test_an_unknown_moment_is_a_named_404(client, db):
    response = client.get(detail_url(7070))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "moment_not_found"
