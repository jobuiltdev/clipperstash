"""Tests for user token refresh, validation and the bounded retry cycle."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.twitch.client import OAUTH_TOKEN_URL, OAUTH_VALIDATE_URL
from apps.twitch.exceptions import TwitchAPIError, TwitchAuthenticationError
from apps.twitch.services import (
    helix_get_as_connection,
    refresh_connection,
    validate_connection,
)
from apps.twitch.tests.conftest import (
    FAKE_ACCESS_TOKEN,
    FAKE_ACCESS_TOKEN_2,
    FAKE_REFRESH_TOKEN,
    FAKE_REFRESH_TOKEN_2,
    HELIX_USERS_URL,
    json_response,
    routed_transport,
    token_payload,
    users_payload,
)

pytestmark = pytest.mark.django_db


# -- local expiry is metadata, not a refresh trigger -------------------------


def test_successful_request_uses_the_stored_token_without_refreshing(make_client, connection):
    transport = routed_transport({HELIX_USERS_URL: json_response(200, users_payload())})

    helix_get_as_connection(connection, "users", client=make_client(transport))

    assert transport.call_count == 1
    assert transport.request_for(HELIX_USERS_URL).headers["Authorization"] == (
        f"Bearer {FAKE_ACCESS_TOKEN}"
    )


def test_locally_expired_token_alone_does_not_trigger_a_refresh(make_client, expired_connection):
    """A token Twitch still accepts must be used, however stale it looks locally."""
    transport = routed_transport({HELIX_USERS_URL: json_response(200, users_payload())})

    helix_get_as_connection(expired_connection, "users", client=make_client(transport))

    assert transport.call_count == 1, "no token request may be made without a 401 from Twitch"
    assert transport.request_for(HELIX_USERS_URL).headers["Authorization"] == (
        f"Bearer {FAKE_ACCESS_TOKEN}"
    )
    expired_connection.refresh_from_db()
    assert expired_connection.get_access_token() == FAKE_ACCESS_TOKEN


def test_locally_near_expired_token_alone_does_not_trigger_a_refresh(make_client, connection):
    connection.token_expires_at = timezone.now() + timedelta(seconds=5)
    connection.save(update_fields=["token_expires_at"])
    assert connection.is_token_expired() is True, "precondition: inside the local expiry window"

    transport = routed_transport({HELIX_USERS_URL: json_response(200, users_payload())})

    helix_get_as_connection(connection, "users", client=make_client(transport))

    assert transport.call_count == 1
    connection.refresh_from_db()
    assert connection.get_access_token() == FAKE_ACCESS_TOKEN


def test_expiry_metadata_is_retained_even_when_stale(expired_connection):
    """`token_expires_at` stays persisted; it is simply not a refresh trigger."""
    expired_connection.refresh_from_db()

    assert expired_connection.token_expires_at is not None
    assert expired_connection.is_token_expired() is True


# -- refresh -----------------------------------------------------------------


def test_refresh_uses_the_refresh_token_grant(make_client, expired_connection):
    transport = routed_transport({OAUTH_TOKEN_URL: json_response(200, token_payload())})

    refresh_connection(expired_connection, client=make_client(transport))

    body = transport.request_for(OAUTH_TOKEN_URL).content.decode()
    assert "grant_type=refresh_token" in body


def test_rotated_refresh_token_is_persisted(make_client, expired_connection):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: json_response(
                200,
                token_payload(access_token=FAKE_ACCESS_TOKEN_2, refresh_token=FAKE_REFRESH_TOKEN_2),
            )
        }
    )

    refresh_connection(expired_connection, client=make_client(transport))

    expired_connection.refresh_from_db()
    assert expired_connection.get_refresh_token() == FAKE_REFRESH_TOKEN_2
    assert expired_connection.get_access_token() == FAKE_ACCESS_TOKEN_2


def test_absent_refresh_token_leaves_the_existing_one_intact(make_client, expired_connection):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: json_response(
                200, token_payload(access_token=FAKE_ACCESS_TOKEN_2, refresh_token=None)
            )
        }
    )

    refresh_connection(expired_connection, client=make_client(transport))

    expired_connection.refresh_from_db()
    assert expired_connection.get_refresh_token() == FAKE_REFRESH_TOKEN


def test_refreshed_expiry_is_persisted(make_client, expired_connection):
    previous_expiry = expired_connection.token_expires_at
    transport = routed_transport(
        {OAUTH_TOKEN_URL: json_response(200, token_payload(expires_in=14400))}
    )

    refresh_connection(expired_connection, client=make_client(transport))

    expired_connection.refresh_from_db()
    assert expired_connection.token_expires_at > previous_expiry
    assert expired_connection.is_token_expired() is False


def test_refreshed_scopes_are_persisted(make_client, expired_connection):
    transport = routed_transport(
        {OAUTH_TOKEN_URL: json_response(200, token_payload(scope=("clips:edit",)))}
    )

    refresh_connection(expired_connection, client=make_client(transport))

    expired_connection.refresh_from_db()
    assert expired_connection.scopes == ["clips:edit"], "Twitch's answer is authoritative"
    assert expired_connection.can_read_chat is False


def test_scopes_are_preserved_when_twitch_omits_them(make_client, expired_connection):
    transport = routed_transport({OAUTH_TOKEN_URL: json_response(200, token_payload(scope=()))})

    refresh_connection(expired_connection, client=make_client(transport))

    expired_connection.refresh_from_db()
    assert expired_connection.scopes == ["clips:edit", "user:read:chat"]


def test_failed_refresh_raises_a_typed_authentication_error(make_client, expired_connection):
    transport = routed_transport(
        {OAUTH_TOKEN_URL: json_response(400, {"message": "Invalid refresh token"})}
    )

    with pytest.raises(TwitchAuthenticationError):
        refresh_connection(expired_connection, client=make_client(transport))

    assert transport.call_count == 1, "a failed refresh must not be retried"


def test_failed_refresh_flags_the_connection_for_reauthorization(make_client, expired_connection):
    transport = routed_transport(
        {OAUTH_TOKEN_URL: json_response(400, {"message": "Invalid refresh token"})}
    )

    with pytest.raises(TwitchAuthenticationError):
        refresh_connection(expired_connection, client=make_client(transport))

    expired_connection.refresh_from_db()
    assert expired_connection.requires_reauthorization is True


# -- bounded refresh-and-retry ----------------------------------------------


def test_unauthorized_response_triggers_exactly_one_refresh_and_retry(make_client, connection):
    transport = routed_transport(
        {
            HELIX_USERS_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(200, users_payload()),
            ],
            OAUTH_TOKEN_URL: json_response(
                200, token_payload(access_token=FAKE_ACCESS_TOKEN_2, refresh_token=None)
            ),
        }
    )

    payload = helix_get_as_connection(connection, "users", client=make_client(transport))

    assert payload["data"][0]["id"] == "123456"
    assert transport.call_count == 3, "one failed call, one refresh, one retry"
    token_requests = [
        request
        for request in transport.requests
        if str(request.url).split("?")[0] == OAUTH_TOKEN_URL
    ]
    assert len(token_requests) == 1, "exactly one refresh"


def test_retried_request_uses_the_refreshed_token(make_client, connection):
    transport = routed_transport(
        {
            HELIX_USERS_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(200, users_payload()),
            ],
            OAUTH_TOKEN_URL: json_response(
                200, token_payload(access_token=FAKE_ACCESS_TOKEN_2, refresh_token=None)
            ),
        }
    )

    helix_get_as_connection(connection, "users", client=make_client(transport))

    helix_requests = [
        request
        for request in transport.requests
        if str(request.url).split("?")[0] == HELIX_USERS_URL
    ]
    assert len(helix_requests) == 2, "the original request is retried exactly once"
    assert helix_requests[0].headers["Authorization"] == f"Bearer {FAKE_ACCESS_TOKEN}"
    assert helix_requests[1].headers["Authorization"] == f"Bearer {FAKE_ACCESS_TOKEN_2}"


def test_second_unauthorized_does_not_refresh_again(make_client, connection):
    transport = routed_transport(
        {
            HELIX_USERS_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(401, {"message": "Invalid OAuth token"}),
            ],
            OAUTH_TOKEN_URL: json_response(200, token_payload()),
        }
    )

    with pytest.raises(TwitchAuthenticationError):
        helix_get_as_connection(connection, "users", client=make_client(transport))

    assert transport.call_count == 3, "the cycle must not loop"
    token_requests = [
        request
        for request in transport.requests
        if str(request.url).split("?")[0] == OAUTH_TOKEN_URL
    ]
    assert len(token_requests) == 1, "no second refresh after the retry also fails"


def test_second_unauthorized_flags_the_connection_for_reauthorization(make_client, connection):
    transport = routed_transport(
        {
            HELIX_USERS_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(401, {"message": "Invalid OAuth token"}),
            ],
            OAUTH_TOKEN_URL: json_response(200, token_payload()),
        }
    )

    with pytest.raises(TwitchAuthenticationError):
        helix_get_as_connection(connection, "users", client=make_client(transport))

    connection.refresh_from_db()
    assert connection.requires_reauthorization is True


def test_retry_is_abandoned_when_the_refresh_itself_fails(make_client, connection):
    transport = routed_transport(
        {
            HELIX_USERS_URL: json_response(401, {"message": "Invalid OAuth token"}),
            OAUTH_TOKEN_URL: json_response(400, {"message": "Invalid refresh token"}),
        }
    )

    with pytest.raises(TwitchAuthenticationError):
        helix_get_as_connection(connection, "users", client=make_client(transport))

    assert transport.call_count == 2, "one rejected call, one failed refresh, no further attempts"


def test_failed_refresh_during_retry_flags_the_connection(make_client, connection):
    transport = routed_transport(
        {
            HELIX_USERS_URL: json_response(401, {"message": "Invalid OAuth token"}),
            OAUTH_TOKEN_URL: json_response(400, {"message": "Invalid refresh token"}),
        }
    )

    with pytest.raises(TwitchAuthenticationError):
        helix_get_as_connection(connection, "users", client=make_client(transport))

    connection.refresh_from_db()
    assert connection.requires_reauthorization is True


def test_rotated_refresh_token_is_persisted_during_retry(make_client, connection):
    transport = routed_transport(
        {
            HELIX_USERS_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(200, users_payload()),
            ],
            OAUTH_TOKEN_URL: json_response(
                200,
                token_payload(access_token=FAKE_ACCESS_TOKEN_2, refresh_token=FAKE_REFRESH_TOKEN_2),
            ),
        }
    )

    helix_get_as_connection(connection, "users", client=make_client(transport))

    connection.refresh_from_db()
    assert connection.get_access_token() == FAKE_ACCESS_TOKEN_2
    assert connection.get_refresh_token() == FAKE_REFRESH_TOKEN_2


def test_expiry_metadata_is_updated_during_retry(make_client, expired_connection):
    previous_expiry = expired_connection.token_expires_at
    transport = routed_transport(
        {
            HELIX_USERS_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(200, users_payload()),
            ],
            OAUTH_TOKEN_URL: json_response(200, token_payload(expires_in=14400)),
        }
    )

    helix_get_as_connection(expired_connection, "users", client=make_client(transport))

    expired_connection.refresh_from_db()
    assert expired_connection.token_expires_at > previous_expiry
    assert expired_connection.is_token_expired() is False


def test_non_auth_errors_are_not_retried(make_client, connection):
    transport = routed_transport({HELIX_USERS_URL: json_response(500, {"message": "boom"})})

    with pytest.raises(TwitchAPIError):
        helix_get_as_connection(connection, "users", client=make_client(transport))

    assert transport.call_count == 1


# -- validation --------------------------------------------------------------


def test_valid_token_is_reported(make_client, connection):
    transport = routed_transport(
        {
            OAUTH_VALIDATE_URL: json_response(
                200,
                {
                    "user_id": "123456",
                    "login": "example",
                    "client_id": "test-client-id",
                    "scopes": ["clips:edit"],
                    "expires_in": 14400,
                },
            )
        }
    )

    validation = validate_connection(connection, client=make_client(transport))

    assert validation.user_id == "123456"
    assert validation.login == "example"
    assert validation.scopes == ("clips:edit",)
    assert validation.expires_in == 14400


def test_validation_sends_the_oauth_authorization_scheme(make_client, connection):
    transport = routed_transport(
        {OAUTH_VALIDATE_URL: json_response(200, {"user_id": "1", "login": "a", "expires_in": 1})}
    )

    validate_connection(connection, client=make_client(transport))

    header = transport.request_for(OAUTH_VALIDATE_URL).headers["Authorization"]
    assert header == f"OAuth {FAKE_ACCESS_TOKEN}"


def test_revoked_token_raises_a_typed_authentication_error(make_client, connection):
    transport = routed_transport({OAUTH_VALIDATE_URL: json_response(401, {"message": "invalid"})})

    with pytest.raises(TwitchAuthenticationError) as exc_info:
        validate_connection(connection, client=make_client(transport))

    assert exc_info.value.status_code == 401
    assert transport.call_count == 1, "an invalid token must not be revalidated in a loop"


def test_revoked_token_flags_the_connection_for_reauthorization(make_client, connection):
    transport = routed_transport({OAUTH_VALIDATE_URL: json_response(401, {"message": "invalid"})})

    with pytest.raises(TwitchAuthenticationError):
        validate_connection(connection, client=make_client(transport))

    connection.refresh_from_db()
    assert connection.requires_reauthorization is True


def test_validation_error_text_hides_the_token(make_client, connection):
    transport = routed_transport({OAUTH_VALIDATE_URL: json_response(401, {"message": "invalid"})})

    with pytest.raises(TwitchAuthenticationError) as exc_info:
        validate_connection(connection, client=make_client(transport))

    assert FAKE_ACCESS_TOKEN not in str(exc_info.value)
