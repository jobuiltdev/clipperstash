"""Tests for the Twitch side of streamer resolution."""

from __future__ import annotations

import pytest

from apps.twitch.client import OAUTH_TOKEN_URL, TwitchClient
from apps.twitch.exceptions import TwitchAPIError, TwitchAuthenticationError
from apps.twitch.models import TwitchConnection
from apps.twitch.services import lookup_user_by_login
from apps.twitch.tests.conftest import (
    FAKE_APP_TOKEN,
    HELIX_USERS_URL,
    json_response,
    routed_transport,
    token_payload,
)
from conftest import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET

from .conftest import lookup_transport, twitch_user


def test_lookup_sends_the_login_as_a_query_parameter(make_client):
    transport = lookup_transport()

    lookup_user_by_login("shroud", client=make_client(transport))

    request = transport.request_for(HELIX_USERS_URL)
    assert request.url.params["login"] == "shroud"
    assert str(request.url).startswith("https://api.twitch.tv/helix/users")


def test_lookup_uses_the_app_access_token(make_client):
    transport = lookup_transport()

    lookup_user_by_login("shroud", client=make_client(transport))

    users_request = transport.request_for(HELIX_USERS_URL)
    assert users_request.headers["Authorization"] == f"Bearer {FAKE_APP_TOKEN}"
    assert users_request.headers["Client-Id"] == FAKE_CLIENT_ID

    token_request = transport.request_for(OAUTH_TOKEN_URL)
    assert "grant_type=client_credentials" in token_request.content.decode()


@pytest.mark.django_db
def test_lookup_does_not_require_a_connected_user(make_client):
    """Public identity is app-level data; no operator OAuth is involved."""
    assert TwitchConnection.objects.count() == 0

    transport = lookup_transport()
    identity = lookup_user_by_login("shroud", client=make_client(transport))

    assert identity is not None
    assert TwitchConnection.objects.count() == 0


def test_lookup_parses_a_normal_identity_response(make_client):
    transport = lookup_transport()

    identity = lookup_user_by_login("shroud", client=make_client(transport))

    assert identity.user_id == "37402112"
    assert identity.login == "shroud"
    assert identity.display_name == "shroud"
    assert identity.profile_image_url == "https://static-cdn.example/shroud-profile.png"
    assert identity.broadcaster_type == "partner"
    assert identity.description == "Professional gamer."


def test_empty_data_is_reported_as_not_found(make_client):
    transport = lookup_transport(users=[])

    assert lookup_user_by_login("nobodyhere", client=make_client(transport)) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"data": "not-a-list"},
        {"data": {}},
        {},
    ],
)
def test_malformed_users_payload_is_mapped_to_an_api_error(make_client, payload):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: json_response(
                200, token_payload(access_token=FAKE_APP_TOKEN, refresh_token=None)
            ),
            HELIX_USERS_URL: json_response(200, payload),
        }
    )

    with pytest.raises(TwitchAPIError):
        lookup_user_by_login("shroud", client=make_client(transport))


@pytest.mark.parametrize(
    "entry",
    [
        "not-an-object",
        {"login": "shroud"},
        {"id": "1"},
        {"id": "", "login": ""},
    ],
)
def test_malformed_user_entry_is_mapped_to_an_api_error(make_client, entry):
    transport = lookup_transport(users=[entry])

    with pytest.raises(TwitchAPIError):
        lookup_user_by_login("shroud", client=make_client(transport))


def test_missing_optional_fields_default_to_empty_strings(make_client):
    transport = lookup_transport(users=[{"id": "1", "login": "minimal"}])

    identity = lookup_user_by_login("minimal", client=make_client(transport))

    assert identity.display_name == ""
    assert identity.profile_image_url == ""
    assert identity.broadcaster_type == ""
    assert identity.description == ""


def test_helix_server_error_is_mapped_to_an_api_error(make_client):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: json_response(
                200, token_payload(access_token=FAKE_APP_TOKEN, refresh_token=None)
            ),
            HELIX_USERS_URL: json_response(500, {"message": "Internal Server Error"}),
        }
    )

    with pytest.raises(TwitchAPIError) as exc_info:
        lookup_user_by_login("shroud", client=make_client(transport))

    assert exc_info.value.status_code == 500


def test_rejected_app_token_is_reminted_and_the_lookup_retried_once(make_client):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: [
                json_response(
                    200, token_payload(access_token="STALE-APP-TOKEN", refresh_token=None)
                ),
                json_response(200, token_payload(access_token=FAKE_APP_TOKEN, refresh_token=None)),
            ],
            HELIX_USERS_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(200, {"data": [twitch_user()]}),
            ],
        }
    )

    identity = lookup_user_by_login("shroud", client=make_client(transport))

    assert identity.login == "shroud"
    assert transport.call_count == 4, "token, rejected lookup, new token, retried lookup"


def test_a_second_rejection_is_not_retried_again(make_client):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: [
                json_response(200, token_payload(access_token="A", refresh_token=None)),
                json_response(200, token_payload(access_token="B", refresh_token=None)),
            ],
            HELIX_USERS_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(401, {"message": "Invalid OAuth token"}),
            ],
        }
    )

    with pytest.raises(TwitchAuthenticationError):
        lookup_user_by_login("shroud", client=make_client(transport))

    assert transport.call_count == 4, "the cycle must not loop"


def test_no_token_or_secret_appears_in_lookup_errors(make_client):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: json_response(
                200, token_payload(access_token=FAKE_APP_TOKEN, refresh_token=None)
            ),
            HELIX_USERS_URL: json_response(500, {"message": "Internal Server Error"}),
        }
    )

    with pytest.raises(TwitchAPIError) as exc_info:
        lookup_user_by_login("shroud", client=make_client(transport))

    message = str(exc_info.value)
    assert FAKE_APP_TOKEN not in message
    assert FAKE_CLIENT_SECRET not in message


def test_the_app_token_never_travels_in_a_url(make_client):
    transport = lookup_transport()

    lookup_user_by_login("shroud", client=make_client(transport))

    for request in transport.requests:
        assert FAKE_APP_TOKEN not in str(request.url)
        assert FAKE_CLIENT_SECRET not in str(request.url)


def test_client_returns_none_for_an_empty_data_list():
    transport = routed_transport({HELIX_USERS_URL: json_response(200, {"data": []})})

    result = TwitchClient(transport=transport).get_user_by_login("ghost", access_token="t")

    assert result is None
