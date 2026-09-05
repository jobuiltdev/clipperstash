"""Tests for the session list and session detail endpoints."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.dashboard import state

from .conftest import CHAT_TEXT

pytestmark = pytest.mark.django_db


def sessions_url(streamer_id: int) -> str:
    return reverse("streamers:sessions", args=[streamer_id])


def detail_url(session_id: int) -> str:
    return reverse("dashboard:session-detail", args=[session_id])


# -- the list ----------------------------------------------------------------


def test_a_streamer_sees_only_their_own_sessions(client, populated):
    page = client.get(sessions_url(populated.streamer.pk)).json()["sessions"]

    ids = [row["id"] for row in page["results"]]
    assert set(ids) == {populated.live_session.pk, populated.ended_session.pk}
    assert populated.other_session.pk not in ids
    assert page["count"] == 2


def test_sessions_are_newest_first(client, populated):
    page = client.get(sessions_url(populated.streamer.pk)).json()["sessions"]

    assert [row["id"] for row in page["results"]] == [
        populated.live_session.pk,
        populated.ended_session.pk,
    ]


def test_sessions_can_be_filtered_by_status(client, populated):
    page = client.get(sessions_url(populated.streamer.pk), {"status": "ended"}).json()["sessions"]

    assert [row["id"] for row in page["results"]] == [populated.ended_session.pk]


def test_an_unknown_status_filter_is_refused(client, populated):
    response = client.get(sessions_url(populated.streamer.pk), {"status": "paused"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_query_parameter"


def test_a_streamer_with_no_sessions_yet_gets_an_empty_page(client, populated):
    populated.other_session.delete()

    page = client.get(sessions_url(populated.other_streamer.pk)).json()["sessions"]

    assert page["results"] == []
    assert page["count"] == 0
    assert page["has_more"] is False


def test_an_unknown_streamer_is_a_named_404(client, db):
    response = client.get(sessions_url(4040))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "streamer_not_found"


def test_paging_reports_whether_more_remain(client, populated):
    page = client.get(sessions_url(populated.streamer.pk), {"limit": 1}).json()["sessions"]

    assert len(page["results"]) == 1
    assert page["count"] == 2
    assert page["has_more"] is True

    second = client.get(sessions_url(populated.streamer.pk), {"limit": 1, "offset": 1}).json()[
        "sessions"
    ]
    assert second["has_more"] is False


def test_listing_sessions_costs_a_fixed_number_of_queries(
    client, populated, django_assert_num_queries
):
    with django_assert_num_queries(3):
        client.get(sessions_url(populated.streamer.pk))


# -- the detail --------------------------------------------------------------


def test_session_detail_reports_its_moment_breakdown(client, populated):
    session = client.get(detail_url(populated.live_session.pk)).json()["session"]

    assert session["id"] == populated.live_session.pk
    assert session["streamer"]["username"] == "shroud"
    assert session["counts"]["total"] == 4
    assert session["counts"][state.CREATED] == 1
    assert session["counts"][state.REQUEST_UNKNOWN] == 1
    assert session["counts"][state.FAILED] == 0


def test_session_detail_counts_chat_without_quoting_it(client, populated):
    """How much was said is operational. What was said is not published."""
    session = client.get(detail_url(populated.live_session.pk)).json()["session"]

    assert session["counts"]["chat_messages"] == 3
    assert CHAT_TEXT not in client.get(detail_url(populated.live_session.pk)).content.decode()


def test_an_ended_session_reports_when_it_ended(client, populated):
    session = client.get(detail_url(populated.ended_session.pk)).json()["session"]

    assert session["status"] == "ended"
    assert session["ended_at"] is not None


def test_an_unknown_session_is_a_named_404(client, db):
    response = client.get(detail_url(9090))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
