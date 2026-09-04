"""Tests for `POST /api/streamers/resolve/`."""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from apps.streamers.models import Streamer
from apps.twitch.client import OAUTH_TOKEN_URL
from apps.twitch.tests.conftest import (
    ALL_FAKE_SECRETS,
    FAKE_APP_TOKEN,
    HELIX_USERS_URL,
    json_response,
    routed_transport,
    token_payload,
)
from conftest import FAKE_CLIENT_SECRET

from .conftest import lookup_transport, twitch_user

pytestmark = pytest.mark.django_db


def resolve_url() -> str:
    return reverse("streamers:resolve")


def post(client, value):
    return client.post(
        resolve_url(),
        data=json.dumps({"input": value}),
        content_type="application/json",
    )


# -- success -----------------------------------------------------------------


def test_resolving_a_url_succeeds(client, patch_streamer_client):
    patch_streamer_client(lookup_transport())

    response = post(client, "https://www.twitch.tv/shroud")

    assert response.status_code == 200
    streamer = response.json()["streamer"]
    assert streamer["platform"] == "twitch"
    assert streamer["platform_user_id"] == "37402112"
    assert streamer["username"] == "shroud"
    assert streamer["display_name"] == "shroud"
    assert streamer["channel_url"] == "https://www.twitch.tv/shroud"
    assert streamer["profile_image_url"] == "https://static-cdn.example/shroud-profile.png"
    assert streamer["broadcaster_type"] == "partner"
    assert streamer["description"] == "Professional gamer."
    assert isinstance(streamer["id"], int)


def test_resolving_a_bare_login_succeeds(client, patch_streamer_client):
    patch_streamer_client(lookup_transport())

    response = post(client, "shroud")

    assert response.status_code == 200
    assert response.json()["streamer"]["username"] == "shroud"


def test_the_database_row_matches_the_response(client, patch_streamer_client):
    patch_streamer_client(lookup_transport())

    payload = post(client, "twitch.tv/SHROUD/").json()["streamer"]

    streamer = Streamer.objects.get(pk=payload["id"])
    assert streamer.username == "shroud"
    assert streamer.platform_user_id == "37402112"
    assert streamer.channel_url == "https://www.twitch.tv/shroud"
    assert streamer.is_active is True


def test_repeated_requests_are_idempotent(client, patch_streamer_client):
    patch_streamer_client(lookup_transport())
    first = post(client, "shroud").json()["streamer"]

    patch_streamer_client(lookup_transport())
    second = post(client, "https://twitch.tv/shroud").json()["streamer"]

    assert first["id"] == second["id"]
    assert Streamer.objects.count() == 1


def test_response_exposes_only_the_declared_fields(client, patch_streamer_client):
    patch_streamer_client(lookup_transport())

    streamer = post(client, "shroud").json()["streamer"]

    assert set(streamer) == {
        "id",
        "platform",
        "platform_user_id",
        "username",
        "display_name",
        "channel_url",
        "profile_image_url",
        "broadcaster_type",
        "description",
    }


# -- invalid input -----------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "https://www.twitch.tv/videos/123456",
        "https://www.twitch.tv/directory",
        "https://www.twitch.tv/settings",
        "https://youtube.com/shroud",
        "twitch.tv.evil.example/name",
        "twitch.tv@evil.example/name",
        "https://twitch.tv/",
        "a" * 26,
        "a-b",
    ],
)
def test_invalid_input_returns_400(client, patch_streamer_client, value):
    transport = lookup_transport()
    patch_streamer_client(transport)

    response = post(client, value)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_streamer_input"
    assert response.json()["error"]["message"]
    assert transport.call_count == 0, "invalid input must never reach Twitch"
    assert Streamer.objects.count() == 0


def test_missing_input_field_returns_400(client):
    response = client.post(resolve_url(), data=json.dumps({}), content_type="application/json")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_streamer_input"


def test_non_string_input_returns_400(client):
    response = client.post(
        resolve_url(),
        data=json.dumps({"input": {"nested": "object"}}),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_streamer_input"


def test_get_is_not_allowed(client):
    assert client.get(resolve_url()).status_code == 405


# -- not found ---------------------------------------------------------------


def test_unknown_streamer_returns_404(client, patch_streamer_client):
    patch_streamer_client(lookup_transport(users=[]))

    response = post(client, "nobodyhere")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "streamer_not_found"
    assert Streamer.objects.count() == 0


@pytest.mark.parametrize("login", ["a", "ab"])
def test_short_logins_are_resolved_rather_than_rejected(client, patch_streamer_client, login):
    """The API must not refuse a short login before Twitch has been asked."""
    transport = lookup_transport(users=[twitch_user(user_id="7", login=login, display_name=login)])
    patch_streamer_client(transport)

    response = post(client, login)

    assert response.status_code == 200
    assert response.json()["streamer"]["username"] == login
    assert transport.request_for(HELIX_USERS_URL).url.params["login"] == login


@pytest.mark.parametrize("login", ["a", "ab"])
def test_unresolved_short_login_returns_404_not_400(client, patch_streamer_client, login):
    patch_streamer_client(lookup_transport(users=[]))

    response = post(client, login)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "streamer_not_found"


# -- Twitch unavailable ------------------------------------------------------


def test_unconfigured_twitch_returns_503(client, settings):
    settings.TWITCH_CLIENT_ID = ""

    response = post(client, "shroud")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "twitch_not_configured"
    body = response.content.decode()
    assert "TWITCH_CLIENT_SECRET" not in body


def test_twitch_server_error_returns_503(client, patch_streamer_client):
    patch_streamer_client(
        routed_transport(
            {
                OAUTH_TOKEN_URL: json_response(
                    200, token_payload(access_token=FAKE_APP_TOKEN, refresh_token=None)
                ),
                HELIX_USERS_URL: json_response(500, {"message": "Internal Server Error"}),
            }
        )
    )

    response = post(client, "shroud")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "twitch_unavailable"
    assert "Internal Server Error" not in response.content.decode()


def test_twitch_auth_failure_returns_503(client, patch_streamer_client):
    patch_streamer_client(
        routed_transport({OAUTH_TOKEN_URL: json_response(401, {"message": "bad client secret"})})
    )

    response = post(client, "shroud")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "twitch_unavailable"
    assert "client secret" not in response.content.decode()


def test_malformed_twitch_payload_returns_503(client, patch_streamer_client):
    patch_streamer_client(lookup_transport(users=[{"login": "no-id"}]))

    response = post(client, "shroud")

    assert response.status_code == 503
    assert Streamer.objects.count() == 0


# -- secrets -----------------------------------------------------------------


def test_no_response_contains_token_material(client, patch_streamer_client):
    patch_streamer_client(lookup_transport())
    bodies = [post(client, "shroud").content.decode()]

    patch_streamer_client(lookup_transport(users=[]))
    bodies.append(post(client, "nobodyhere").content.decode())
    bodies.append(post(client, "https://twitch.tv/videos/1").content.decode())

    for body in bodies:
        for secret in (*ALL_FAKE_SECRETS, FAKE_APP_TOKEN, FAKE_CLIENT_SECRET):
            assert secret not in body


def test_error_bodies_never_carry_internal_detail(client, patch_streamer_client):
    patch_streamer_client(
        routed_transport(
            {
                OAUTH_TOKEN_URL: json_response(
                    200, token_payload(access_token=FAKE_APP_TOKEN, refresh_token=None)
                ),
                HELIX_USERS_URL: json_response(500, {"message": "Internal Server Error"}),
            }
        )
    )

    body = post(client, "shroud").content.decode().lower()

    for forbidden in ("traceback", "httpx", "helix", "bearer", "client_secret", "access_token"):
        assert forbidden not in body
