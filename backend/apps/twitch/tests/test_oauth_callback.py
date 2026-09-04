"""Tests for `GET /api/twitch/oauth/callback/`."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from django.urls import reverse

from apps.twitch import oauth
from apps.twitch.client import OAUTH_TOKEN_URL
from apps.twitch.models import TwitchConnection
from apps.twitch.tests.conftest import (
    ALL_FAKE_SECRETS,
    FAKE_ACCESS_TOKEN,
    FAKE_AUTH_CODE,
    FAKE_REFRESH_TOKEN,
    HELIX_USERS_URL,
    json_response,
    routed_transport,
    token_payload,
    users_payload,
)
from conftest import FAKE_CLIENT_SECRET

pytestmark = pytest.mark.django_db


def callback_url() -> str:
    return reverse("twitch:oauth-callback")


def outcome(response) -> dict[str, list[str]]:
    return parse_qs(urlparse(response["Location"]).query)


def successful_transport():
    return routed_transport(
        {
            OAUTH_TOKEN_URL: json_response(200, token_payload()),
            HELIX_USERS_URL: json_response(200, users_payload()),
        }
    )


def test_successful_callback_persists_the_connection(client, patch_service_client):
    patch_service_client(successful_transport())
    state = oauth.create_state()

    response = client.get(callback_url(), {"code": FAKE_AUTH_CODE, "state": state})

    assert response.status_code == 302
    assert outcome(response)["twitch"] == ["connected"]

    connection = TwitchConnection.objects.current()
    assert connection is not None
    assert connection.twitch_user_id == "123456"
    assert connection.login == "example"
    assert connection.display_name == "Example"
    assert connection.scopes == ["clips:edit"]
    assert connection.requires_reauthorization is False


def test_successful_callback_stores_both_tokens_and_expiry(client, patch_service_client):
    patch_service_client(successful_transport())
    state = oauth.create_state()

    client.get(callback_url(), {"code": FAKE_AUTH_CODE, "state": state})

    connection = TwitchConnection.objects.current()
    assert connection.get_access_token() == FAKE_ACCESS_TOKEN
    assert connection.get_refresh_token() == FAKE_REFRESH_TOKEN
    assert connection.is_token_expired() is False


def test_callback_exchanges_the_code_server_side(client, patch_service_client):
    transport = successful_transport()
    patch_service_client(transport)
    state = oauth.create_state()

    client.get(callback_url(), {"code": FAKE_AUTH_CODE, "state": state})

    body = transport.request_for(OAUTH_TOKEN_URL).content.decode()
    assert "grant_type=authorization_code" in body
    # The exchange happens over the backend's own connection to Twitch.
    assert transport.request_for(OAUTH_TOKEN_URL).method == "POST"


def test_callback_redirects_to_the_frontend(client, patch_service_client, settings):
    patch_service_client(successful_transport())
    state = oauth.create_state()

    response = client.get(callback_url(), {"code": FAKE_AUTH_CODE, "state": state})

    assert response["Location"].startswith(settings.FRONTEND_BASE_URL)


def test_response_never_contains_token_material(client, patch_service_client):
    patch_service_client(successful_transport())
    state = oauth.create_state()

    response = client.get(callback_url(), {"code": FAKE_AUTH_CODE, "state": state})

    surface = response["Location"] + response.content.decode()
    for secret in ALL_FAKE_SECRETS:
        assert secret not in surface
    assert FAKE_CLIENT_SECRET not in surface


def test_missing_state_is_rejected(client, patch_service_client):
    transport = successful_transport()
    patch_service_client(transport)

    response = client.get(callback_url(), {"code": FAKE_AUTH_CODE})

    assert outcome(response)["reason"] == ["invalid_state"]
    assert transport.call_count == 0, "no code may be exchanged without a valid state"
    assert TwitchConnection.objects.count() == 0


def test_mismatched_state_is_rejected(client, patch_service_client):
    transport = successful_transport()
    patch_service_client(transport)
    oauth.create_state()

    response = client.get(
        callback_url(), {"code": FAKE_AUTH_CODE, "state": "a-state-from-somewhere-else"}
    )

    assert outcome(response)["reason"] == ["invalid_state"]
    assert transport.call_count == 0
    assert TwitchConnection.objects.count() == 0


def test_reused_state_is_rejected(client, patch_service_client):
    patch_service_client(successful_transport())
    state = oauth.create_state()

    first = client.get(callback_url(), {"code": FAKE_AUTH_CODE, "state": state})
    assert outcome(first)["twitch"] == ["connected"]

    patch_service_client(successful_transport())
    second = client.get(callback_url(), {"code": FAKE_AUTH_CODE, "state": state})

    assert outcome(second)["reason"] == ["invalid_state"]
    assert TwitchConnection.objects.count() == 1


def test_missing_code_is_rejected(client, patch_service_client):
    transport = successful_transport()
    patch_service_client(transport)
    state = oauth.create_state()

    response = client.get(callback_url(), {"state": state})

    assert outcome(response)["reason"] == ["missing_code"]
    assert transport.call_count == 0
    assert TwitchConnection.objects.count() == 0


def test_access_denied_is_handled_cleanly(client, patch_service_client):
    transport = successful_transport()
    patch_service_client(transport)
    state = oauth.create_state()

    response = client.get(callback_url(), {"error": "access_denied", "state": state})

    assert response.status_code == 302
    assert outcome(response)["twitch"] == ["denied"]
    assert outcome(response)["reason"] == ["access_denied"]
    assert transport.call_count == 0
    assert TwitchConnection.objects.count() == 0


def test_denied_state_cannot_be_replayed(client, patch_service_client):
    patch_service_client(successful_transport())
    state = oauth.create_state()

    client.get(callback_url(), {"error": "access_denied", "state": state})
    response = client.get(callback_url(), {"code": FAKE_AUTH_CODE, "state": state})

    assert outcome(response)["reason"] == ["invalid_state"]


def test_token_exchange_failure_is_handled(client, patch_service_client):
    patch_service_client(
        routed_transport({OAUTH_TOKEN_URL: json_response(400, {"message": "invalid code"})})
    )
    state = oauth.create_state()

    response = client.get(callback_url(), {"code": FAKE_AUTH_CODE, "state": state})

    assert response.status_code == 302
    assert outcome(response)["reason"] == ["exchange_failed"]
    assert TwitchConnection.objects.count() == 0


def test_rejected_credentials_during_exchange_are_handled(client, patch_service_client):
    patch_service_client(
        routed_transport({OAUTH_TOKEN_URL: json_response(401, {"message": "unauthorized"})})
    )
    state = oauth.create_state()

    response = client.get(callback_url(), {"code": FAKE_AUTH_CODE, "state": state})

    assert outcome(response)["reason"] == ["exchange_failed"]
    assert TwitchConnection.objects.count() == 0


def test_identity_lookup_failure_is_handled(client, patch_service_client):
    patch_service_client(
        routed_transport(
            {
                OAUTH_TOKEN_URL: json_response(200, token_payload()),
                HELIX_USERS_URL: json_response(500, {"message": "boom"}),
            }
        )
    )
    state = oauth.create_state()

    response = client.get(callback_url(), {"code": FAKE_AUTH_CODE, "state": state})

    assert outcome(response)["reason"] == ["exchange_failed"]
    assert TwitchConnection.objects.count() == 0


def test_unconfigured_application_is_handled(client, patch_service_client, settings):
    settings.TWITCH_CLIENT_ID = ""
    state = oauth.create_state()

    response = client.get(callback_url(), {"code": FAKE_AUTH_CODE, "state": state})

    assert outcome(response)["reason"] == ["not_configured"]


def test_reconnecting_the_same_account_updates_in_place(client, patch_service_client):
    patch_service_client(successful_transport())
    client.get(callback_url(), {"code": FAKE_AUTH_CODE, "state": oauth.create_state()})

    patch_service_client(
        routed_transport(
            {
                OAUTH_TOKEN_URL: json_response(200, token_payload(access_token="NEW-TOKEN")),
                HELIX_USERS_URL: json_response(
                    200, users_payload(login="renamed", display_name="Renamed")
                ),
            }
        )
    )
    client.get(callback_url(), {"code": FAKE_AUTH_CODE, "state": oauth.create_state()})

    assert TwitchConnection.objects.count() == 1
    connection = TwitchConnection.objects.current()
    assert connection.login == "renamed"
    assert connection.get_access_token() == "NEW-TOKEN"
