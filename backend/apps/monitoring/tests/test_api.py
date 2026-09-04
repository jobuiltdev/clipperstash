"""Tests for `POST /api/streamers/<id>/observe/`."""

from __future__ import annotations

import httpx
import pytest
from django.urls import reverse

from apps.monitoring.models import StreamSession, StreamSessionStatus
from apps.twitch.tests.conftest import ALL_FAKE_SECRETS, json_response
from conftest import FAKE_CLIENT_SECRET

from .conftest import (
    FAKE_APP_TOKEN,
    TWITCH_STREAM_ID,
    failing_transport,
    live_transport,
    offline_transport,
    twitch_stream,
)

pytestmark = pytest.mark.django_db


def observe_url(streamer_id: int) -> str:
    return reverse("streamers:observe", args=[streamer_id])


def post(client, streamer_id: int):
    return client.post(observe_url(streamer_id), content_type="application/json")


# -- live --------------------------------------------------------------------


def test_live_response(client, patch_observation_client, streamer):
    patch_observation_client(live_transport())

    response = post(client, streamer.pk)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "live"
    assert payload["streamer"] == {
        "id": streamer.pk,
        "username": "shroud",
        "display_name": "shroud",
    }

    stream = payload["stream"]
    assert stream["platform_stream_id"] == TWITCH_STREAM_ID
    assert stream["title"] == "Ranked grind"
    assert stream["category_id"] == "509658"
    assert stream["category_name"] == "Just Chatting"
    assert stream["language"] == "en"
    assert stream["viewer_count"] == 1200
    assert stream["is_mature"] is False
    assert stream["started_at"].startswith("2026-09-04T09:00:00")
    assert stream["session_id"] == StreamSession.objects.get().pk


def test_live_response_exposes_only_the_declared_fields(client, patch_observation_client, streamer):
    patch_observation_client(live_transport())

    payload = post(client, streamer.pk).json()

    assert set(payload) == {"status", "streamer", "stream"}
    assert set(payload["streamer"]) == {"id", "username", "display_name"}
    assert set(payload["stream"]) == {
        "session_id",
        "platform_stream_id",
        "started_at",
        "title",
        "category_id",
        "category_name",
        "language",
        "viewer_count",
        "is_mature",
    }


def test_repeated_live_requests_return_the_same_session_id(
    client, patch_observation_client, streamer
):
    patch_observation_client(live_transport())
    first = post(client, streamer.pk).json()["stream"]["session_id"]

    patch_observation_client(live_transport([twitch_stream(viewer_count=99)]))
    second_payload = post(client, streamer.pk).json()["stream"]

    assert second_payload["session_id"] == first
    assert second_payload["viewer_count"] == 99
    assert StreamSession.objects.count() == 1


# -- offline -----------------------------------------------------------------


def test_offline_response(client, patch_observation_client, streamer):
    patch_observation_client(offline_transport())

    response = post(client, streamer.pk)

    assert response.status_code == 200
    assert response.json() == {
        "status": "offline",
        "streamer": {"id": streamer.pk, "username": "shroud", "display_name": "shroud"},
        "stream": None,
    }
    assert StreamSession.objects.count() == 0


def test_offline_request_closes_a_live_session(client, patch_observation_client, streamer):
    patch_observation_client(live_transport())
    post(client, streamer.pk)

    patch_observation_client(offline_transport())
    response = post(client, streamer.pk)

    assert response.json()["status"] == "offline"
    session = StreamSession.objects.get()
    assert session.status == StreamSessionStatus.ENDED
    assert session.ended_at is not None


def test_repeated_offline_requests_stay_offline(client, patch_observation_client, streamer):
    patch_observation_client(offline_transport())

    for _ in range(3):
        assert post(client, streamer.pk).json()["status"] == "offline"

    assert StreamSession.objects.count() == 0


# -- unknown streamer --------------------------------------------------------


def test_unknown_streamer_returns_404(client, patch_observation_client, db):
    transport = live_transport()
    patch_observation_client(transport)

    response = post(client, 9999)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "streamer_not_found"
    assert transport.call_count == 0, "an unknown streamer must never reach Twitch"


def test_get_is_not_allowed(client, streamer):
    assert client.get(observe_url(streamer.pk)).status_code == 405


# -- unavailable -------------------------------------------------------------


def unavailable_transports():
    def explode(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    return [
        pytest.param(failing_transport(json_response(500, {"message": "boom"})), id="server-error"),
        pytest.param(failing_transport(explode), id="timeout"),
        pytest.param(failing_transport(json_response(200, {"data": "nonsense"})), id="malformed"),
        pytest.param(
            failing_transport(json_response(401, {"message": "Invalid OAuth token"})),
            id="auth-failure",
        ),
    ]


@pytest.mark.parametrize("transport", unavailable_transports())
def test_twitch_failure_returns_503_not_offline(
    client, patch_observation_client, streamer, transport
):
    patch_observation_client(transport)

    response = post(client, streamer.pk)

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "stream_state_unavailable"
    assert payload["error"]["message"]
    assert "status" not in payload, "an unavailable check is never a live/offline result"
    assert "offline" not in response.content.decode().lower()


@pytest.mark.parametrize("transport", unavailable_transports())
def test_twitch_failure_leaves_a_live_session_open(
    client, patch_observation_client, streamer, transport
):
    patch_observation_client(live_transport())
    post(client, streamer.pk)

    patch_observation_client(transport)
    assert post(client, streamer.pk).status_code == 503

    session = StreamSession.objects.get()
    assert session.status == StreamSessionStatus.LIVE
    assert session.ended_at is None


def test_unconfigured_twitch_returns_503(client, settings, streamer):
    settings.TWITCH_CLIENT_ID = ""

    response = post(client, streamer.pk)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "twitch_not_configured"
    assert "TWITCH_CLIENT_SECRET" not in response.content.decode()


# -- secrets -----------------------------------------------------------------


def test_no_response_contains_token_material(client, patch_observation_client, streamer):
    bodies = []

    patch_observation_client(live_transport())
    bodies.append(post(client, streamer.pk).content.decode())

    patch_observation_client(offline_transport())
    bodies.append(post(client, streamer.pk).content.decode())

    patch_observation_client(failing_transport(json_response(500, {"message": "boom"})))
    bodies.append(post(client, streamer.pk).content.decode())

    for body in bodies:
        for secret in (*ALL_FAKE_SECRETS, FAKE_APP_TOKEN, FAKE_CLIENT_SECRET):
            assert secret not in body


def test_error_bodies_never_carry_internal_detail(client, patch_observation_client, streamer):
    patch_observation_client(
        failing_transport(json_response(500, {"message": "Internal Server Error"}))
    )

    body = post(client, streamer.pk).content.decode()

    assert "Internal Server Error" not in body
    lowered = body.lower()
    for forbidden in ("traceback", "httpx", "helix", "bearer", "client_secret", "access_token"):
        assert forbidden not in lowered
