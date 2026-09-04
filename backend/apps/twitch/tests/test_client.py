"""Tests for the centralized Twitch HTTP client."""

from __future__ import annotations

import httpx
import pytest

from apps.twitch.client import (
    DEFAULT_TIMEOUT_SECONDS,
    OAUTH_TOKEN_URL,
    TwitchClient,
    TwitchCredentials,
    normalize_scopes,
)
from apps.twitch.exceptions import (
    TwitchAPIError,
    TwitchAuthenticationError,
    TwitchConfigurationError,
)
from apps.twitch.tests.conftest import (
    FAKE_ACCESS_TOKEN,
    HELIX_USERS_URL,
    json_response,
    routed_transport,
    users_payload,
)
from conftest import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET


def test_helix_request_sends_client_id_and_bearer_token(make_client):
    transport = routed_transport({HELIX_USERS_URL: json_response(200, users_payload())})

    make_client(transport).get_authenticated_user(FAKE_ACCESS_TOKEN)

    request = transport.request_for(HELIX_USERS_URL)
    assert request.headers["Client-Id"] == FAKE_CLIENT_ID
    assert request.headers["Authorization"] == f"Bearer {FAKE_ACCESS_TOKEN}"


def test_helix_request_targets_the_helix_base_url(make_client):
    transport = routed_transport({HELIX_USERS_URL: json_response(200, users_payload())})

    make_client(transport).helix_get("users", access_token=FAKE_ACCESS_TOKEN)

    assert str(transport.request_for(HELIX_USERS_URL).url).startswith(
        "https://api.twitch.tv/helix/users"
    )


def test_client_configures_a_request_timeout():
    client = TwitchClient()

    assert client.timeout == DEFAULT_TIMEOUT_SECONDS
    assert client.timeout > 0
    # The timeout is handed to the underlying transport, not just stored.
    with client._client() as underlying:
        assert underlying.timeout.read == DEFAULT_TIMEOUT_SECONDS
        assert underlying.timeout.connect == DEFAULT_TIMEOUT_SECONDS


def test_get_authenticated_user_returns_identity(make_client):
    transport = routed_transport(
        {HELIX_USERS_URL: json_response(200, users_payload(user_id="99", login="streamer"))}
    )

    identity = make_client(transport).get_authenticated_user(FAKE_ACCESS_TOKEN)

    assert identity.user_id == "99"
    assert identity.login == "streamer"
    assert identity.display_name == "Example"


def test_get_authenticated_user_sends_no_query_parameters(make_client):
    transport = routed_transport({HELIX_USERS_URL: json_response(200, users_payload())})

    make_client(transport).get_authenticated_user(FAKE_ACCESS_TOKEN)

    assert transport.request_for(HELIX_USERS_URL).url.params == httpx.QueryParams()


def test_server_error_is_mapped_to_an_integration_exception(make_client):
    transport = routed_transport(
        {HELIX_USERS_URL: json_response(500, {"message": "Internal Server Error"})}
    )

    with pytest.raises(TwitchAPIError) as exc_info:
        make_client(transport).helix_get("users", access_token=FAKE_ACCESS_TOKEN)

    assert exc_info.value.status_code == 500
    assert "Internal Server Error" in str(exc_info.value)


def test_unauthorized_response_is_mapped_to_an_authentication_error(make_client):
    transport = routed_transport(
        {HELIX_USERS_URL: json_response(401, {"message": "Invalid OAuth token"})}
    )

    with pytest.raises(TwitchAuthenticationError) as exc_info:
        make_client(transport).helix_get("users", access_token=FAKE_ACCESS_TOKEN)

    assert exc_info.value.status_code == 401


def test_forbidden_response_is_mapped_to_an_authentication_error(make_client):
    transport = routed_transport({HELIX_USERS_URL: json_response(403, {"message": "Forbidden"})})

    with pytest.raises(TwitchAuthenticationError):
        make_client(transport).helix_get("users", access_token=FAKE_ACCESS_TOKEN)


def test_exception_text_never_contains_the_token_or_client_secret(make_client):
    transport = routed_transport(
        {HELIX_USERS_URL: json_response(500, {"message": "Internal Server Error"})}
    )

    with pytest.raises(TwitchAPIError) as exc_info:
        make_client(transport).helix_get("users", access_token=FAKE_ACCESS_TOKEN)

    message = str(exc_info.value)
    assert FAKE_ACCESS_TOKEN not in message
    assert FAKE_CLIENT_SECRET not in message


def test_non_json_response_is_mapped_to_an_integration_exception(make_client):
    transport = routed_transport({HELIX_USERS_URL: httpx.Response(200, text="<html>nope</html>")})

    with pytest.raises(TwitchAPIError, match="non-JSON"):
        make_client(transport).helix_get("users", access_token=FAKE_ACCESS_TOKEN)


def test_transport_failure_is_mapped_to_an_integration_exception(make_client):
    def explode(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    transport = routed_transport({HELIX_USERS_URL: explode})

    with pytest.raises(TwitchAPIError, match="Could not reach Twitch"):
        make_client(transport).helix_get("users", access_token=FAKE_ACCESS_TOKEN)


def test_empty_users_payload_is_rejected(make_client):
    transport = routed_transport({HELIX_USERS_URL: json_response(200, {"data": []})})

    with pytest.raises(TwitchAPIError, match="no user"):
        make_client(transport).get_authenticated_user(FAKE_ACCESS_TOKEN)


def test_token_response_without_access_token_is_rejected(make_client):
    transport = routed_transport({OAUTH_TOKEN_URL: json_response(200, {"expires_in": 100})})

    with pytest.raises(TwitchAPIError, match="no access token"):
        make_client(transport).fetch_app_access_token()


def test_token_response_string_hides_the_token(make_client):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: json_response(
                200,
                {
                    "access_token": FAKE_ACCESS_TOKEN,
                    "expires_in": 100,
                    "token_type": "bearer",
                },
            )
        }
    )

    grant = make_client(transport).fetch_app_access_token()

    assert FAKE_ACCESS_TOKEN not in str(grant)
    assert FAKE_ACCESS_TOKEN not in repr(grant)


def test_missing_configuration_raises_a_configuration_error(settings):
    settings.TWITCH_CLIENT_ID = ""

    with pytest.raises(TwitchConfigurationError, match="TWITCH_CLIENT_ID"):
        TwitchCredentials.from_settings()


def test_missing_secret_and_redirect_uri_are_both_reported(settings):
    settings.TWITCH_CLIENT_SECRET = ""
    settings.TWITCH_REDIRECT_URI = ""

    with pytest.raises(TwitchConfigurationError) as exc_info:
        TwitchCredentials.from_settings()

    message = str(exc_info.value)
    assert "TWITCH_CLIENT_SECRET" in message
    assert "TWITCH_REDIRECT_URI" in message


def test_credentials_repr_hides_the_client_secret():
    credentials = TwitchCredentials.from_settings()

    assert FAKE_CLIENT_SECRET not in repr(credentials)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("clips:edit", ("clips:edit",)),
        ("clips:edit user:read:chat", ("clips:edit", "user:read:chat")),
        (["clips:edit"], ("clips:edit",)),
        (None, ()),
        ("", ()),
    ],
)
def test_scope_normalization(value, expected):
    assert normalize_scopes(value) == expected
