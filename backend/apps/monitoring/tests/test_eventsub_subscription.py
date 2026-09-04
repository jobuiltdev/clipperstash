"""Tests for creating the chat subscription over Helix."""

from __future__ import annotations

import json

import pytest
from django.utils import timezone

from apps.twitch.client import OAUTH_TOKEN_URL, TwitchClient
from apps.twitch.exceptions import TwitchAPIError, TwitchAuthenticationError
from apps.twitch.services import create_chat_message_subscription
from apps.twitch.tests.conftest import (
    FAKE_ACCESS_TOKEN,
    FAKE_ACCESS_TOKEN_2,
    FAKE_APP_TOKEN,
    json_response,
    routed_transport,
    token_payload,
)
from conftest import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET

from .chat_fixtures import (
    BROADCASTER_ID,
    HELIX_EVENTSUB_URL,
    SESSION_ID,
    subscription_payload,
    subscription_transport,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def make_client():
    def factory(transport):
        return TwitchClient(transport=transport)

    return factory


def subscribe(connection, transport, make_client):
    return create_chat_message_subscription(
        connection,
        broadcaster_user_id=BROADCASTER_ID,
        session_id=SESSION_ID,
        client=make_client(transport),
    )


def request_body(transport) -> dict:
    return json.loads(transport.request_for(HELIX_EVENTSUB_URL).content.decode())


# -- request shape -----------------------------------------------------------


def test_subscription_targets_the_eventsub_endpoint(connection, make_client):
    transport = subscription_transport()

    subscribe(connection, transport, make_client)

    request = transport.request_for(HELIX_EVENTSUB_URL)
    assert request.method == "POST"
    assert str(request.url) == "https://api.twitch.tv/helix/eventsub/subscriptions"


def test_subscription_uses_the_connected_user_token_not_the_app_token(connection, make_client):
    transport = subscription_transport()

    subscribe(connection, transport, make_client)

    request = transport.request_for(HELIX_EVENTSUB_URL)
    assert request.headers["Authorization"] == f"Bearer {FAKE_ACCESS_TOKEN}"
    assert request.headers["Client-Id"] == FAKE_CLIENT_ID

    called = {str(sent.url).split("?")[0] for sent in transport.requests}
    assert OAUTH_TOKEN_URL not in called, "no app token may be minted for this call"
    assert FAKE_APP_TOKEN not in request.headers["Authorization"]


def test_subscription_body_matches_the_documented_contract(connection, make_client):
    transport = subscription_transport()

    subscribe(connection, transport, make_client)

    body = request_body(transport)
    assert body["type"] == "channel.chat.message"
    assert body["version"] == "1"
    assert body["condition"] == {
        "broadcaster_user_id": BROADCASTER_ID,
        "user_id": connection.twitch_user_id,
    }
    assert body["transport"] == {"method": "websocket", "session_id": SESSION_ID}


def test_condition_user_id_is_the_connected_account(connection, make_client):
    transport = subscription_transport()

    subscribe(connection, transport, make_client)

    assert request_body(transport)["condition"]["user_id"] == "123456"


def test_identities_are_numeric_ids_not_logins(connection, make_client):
    transport = subscription_transport()

    subscribe(connection, transport, make_client)

    condition = request_body(transport)["condition"]
    assert "example" not in json.dumps(condition), "logins are never used as identity keys"
    assert "shroud" not in json.dumps(condition)


def test_subscription_result_is_parsed(connection, make_client):
    subscription = subscribe(connection, subscription_transport(), make_client)

    assert subscription.subscription_id == "sub-1"
    assert subscription.subscription_type == "channel.chat.message"
    assert subscription.version == "1"
    assert subscription.status == "enabled"


@pytest.mark.parametrize(
    "payload",
    [{"data": []}, {"data": "nope"}, {}, {"data": [{"status": "enabled"}]}],
)
def test_malformed_subscription_response_is_rejected(connection, make_client, payload):
    transport = subscription_transport(json_response(202, payload))

    with pytest.raises(TwitchAPIError):
        subscribe(connection, transport, make_client)


# -- bounded refresh and retry -----------------------------------------------


def test_rejected_token_refreshes_once_and_retries(connection, make_client):
    transport = routed_transport(
        {
            HELIX_EVENTSUB_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(202, subscription_payload()),
            ],
            OAUTH_TOKEN_URL: json_response(
                200, token_payload(access_token=FAKE_ACCESS_TOKEN_2, refresh_token=None)
            ),
        }
    )

    subscription = subscribe(connection, transport, make_client)

    assert subscription.subscription_id == "sub-1"
    assert transport.call_count == 3, "rejected call, one refresh, one retry"

    posts = [
        sent for sent in transport.requests if str(sent.url).split("?")[0] == HELIX_EVENTSUB_URL
    ]
    assert posts[0].headers["Authorization"] == f"Bearer {FAKE_ACCESS_TOKEN}"
    assert posts[1].headers["Authorization"] == f"Bearer {FAKE_ACCESS_TOKEN_2}"

    connection.refresh_from_db()
    assert connection.get_access_token() == FAKE_ACCESS_TOKEN_2


def test_a_second_rejection_stops_and_flags_the_connection(connection, make_client):
    transport = routed_transport(
        {
            HELIX_EVENTSUB_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(401, {"message": "Invalid OAuth token"}),
            ],
            OAUTH_TOKEN_URL: json_response(200, token_payload()),
        }
    )

    with pytest.raises(TwitchAuthenticationError):
        subscribe(connection, transport, make_client)

    assert transport.call_count == 3, "the cycle must not loop"
    connection.refresh_from_db()
    assert connection.requires_reauthorization is True


def test_a_failed_refresh_stops_and_flags_the_connection(connection, make_client):
    transport = routed_transport(
        {
            HELIX_EVENTSUB_URL: json_response(401, {"message": "Invalid OAuth token"}),
            OAUTH_TOKEN_URL: json_response(400, {"message": "Invalid refresh token"}),
        }
    )

    with pytest.raises(TwitchAuthenticationError):
        subscribe(connection, transport, make_client)

    assert transport.call_count == 2
    connection.refresh_from_db()
    assert connection.requires_reauthorization is True


def test_a_locally_expired_token_is_still_used_first(connection, make_client):
    """Expiry metadata does not pre-empt Twitch, as established in Milestone 1."""
    connection.token_expires_at = timezone.now() - timezone.timedelta(hours=1)
    connection.save(update_fields=["token_expires_at"])

    transport = subscription_transport()
    subscribe(connection, transport, make_client)

    assert transport.request_for(HELIX_EVENTSUB_URL).headers["Authorization"] == (
        f"Bearer {FAKE_ACCESS_TOKEN}"
    )
    assert transport.call_count == 1, "no refresh happens without a 401"


# -- secrets ------------------------------------------------------------------


def test_no_secret_appears_in_subscription_errors(connection, make_client):
    transport = subscription_transport(json_response(500, {"message": "Internal Server Error"}))

    with pytest.raises(TwitchAPIError) as exc_info:
        subscribe(connection, transport, make_client)

    message = str(exc_info.value)
    assert FAKE_ACCESS_TOKEN not in message
    assert FAKE_CLIENT_SECRET not in message


def test_no_token_travels_in_the_subscription_url(connection, make_client):
    transport = subscription_transport()

    subscribe(connection, transport, make_client)

    for request in transport.requests:
        assert FAKE_ACCESS_TOKEN not in str(request.url)
        assert FAKE_CLIENT_SECRET not in str(request.url)
