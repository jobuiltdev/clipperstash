"""Tests for the Twitch Get Streams integration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from django.utils import timezone

from apps.twitch.client import OAUTH_TOKEN_URL, TwitchClient
from apps.twitch.exceptions import TwitchAPIError, TwitchAuthenticationError
from apps.twitch.models import TwitchConnection
from apps.twitch.services import lookup_stream_by_user_id
from apps.twitch.tests.conftest import (
    FAKE_ACCESS_TOKEN,
    FAKE_APP_TOKEN,
    json_response,
    routed_transport,
    token_payload,
)
from conftest import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET

from .conftest import (
    HELIX_STREAMS_URL,
    TWITCH_USER_ID,
    failing_transport,
    live_transport,
    offline_transport,
    token_route,
    twitch_stream,
)


@pytest.fixture
def make_client():
    def factory(transport):
        return TwitchClient(transport=transport)

    return factory


# -- request shape -----------------------------------------------------------


def test_lookup_targets_the_streams_endpoint(make_client):
    transport = live_transport()

    lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(transport))

    request = transport.request_for(HELIX_STREAMS_URL)
    assert str(request.url).startswith("https://api.twitch.tv/helix/streams")


def test_lookup_sends_the_user_id_as_a_query_parameter(make_client):
    transport = live_transport()

    lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(transport))

    request = transport.request_for(HELIX_STREAMS_URL)
    assert request.url.params["user_id"] == TWITCH_USER_ID
    assert "login" not in request.url.params
    assert "user_login" not in request.url.params


def test_lookup_uses_the_app_access_token(make_client):
    transport = live_transport()

    lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(transport))

    request = transport.request_for(HELIX_STREAMS_URL)
    assert request.headers["Authorization"] == f"Bearer {FAKE_APP_TOKEN}"
    assert request.headers["Client-Id"] == FAKE_CLIENT_ID
    assert "grant_type=client_credentials" in (
        transport.request_for(OAUTH_TOKEN_URL).content.decode()
    )


@pytest.mark.django_db
def test_lookup_ignores_any_connected_user_token(make_client):
    """A connected operator account exists, and is deliberately not used."""
    TwitchConnection.objects.create(
        twitch_user_id="1",
        login="operator",
        display_name="Operator",
        access_token=FAKE_ACCESS_TOKEN,
        refresh_token="refresh",
        token_expires_at=timezone.now() + timedelta(hours=4),
        scopes=["clips:edit"],
    )
    transport = live_transport()

    lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(transport))

    request = transport.request_for(HELIX_STREAMS_URL)
    assert request.headers["Authorization"] == f"Bearer {FAKE_APP_TOKEN}"
    assert FAKE_ACCESS_TOKEN not in request.headers["Authorization"]
    assert TwitchConnection.objects.count() == 1


# -- parsing -----------------------------------------------------------------


def test_live_response_is_parsed(make_client):
    stream = lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(live_transport()))

    assert stream is not None
    assert stream.stream_id == "41375541868"
    assert stream.user_id == TWITCH_USER_ID
    assert stream.user_login == "shroud"
    assert stream.title == "Ranked grind"
    assert stream.game_id == "509658"
    assert stream.game_name == "Just Chatting"
    assert stream.viewer_count == 1200
    assert stream.language == "en"
    assert stream.is_mature is False
    assert stream.stream_type == "live"
    assert stream.started_at == datetime(2026, 9, 4, 9, 0, tzinfo=UTC)
    assert stream.started_at.tzinfo is not None


def test_empty_data_means_offline(make_client):
    assert lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(offline_transport())) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"data": "not-a-list"},
        {"data": {}},
        {},
    ],
)
def test_malformed_streams_payload_raises(make_client, payload):
    transport = routed_transport({**token_route(), HELIX_STREAMS_URL: json_response(200, payload)})

    with pytest.raises(TwitchAPIError):
        lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(transport))


@pytest.mark.parametrize(
    "entry",
    [
        "not-an-object",
        {"user_id": TWITCH_USER_ID},
        {"id": "1"},
        {"id": "", "user_id": ""},
    ],
)
def test_malformed_stream_entry_raises(make_client, entry):
    with pytest.raises(TwitchAPIError):
        lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(live_transport([entry])))


def test_stream_for_a_different_broadcaster_is_rejected(make_client):
    """A mismatched user id is an integration fault, not an offline signal."""
    transport = live_transport([twitch_stream(user_id="999")])

    with pytest.raises(TwitchAPIError, match="different broadcaster"):
        lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(transport))


@pytest.mark.parametrize("stream_type", ["", "rerun", "vodcast", "playlist"])
def test_stream_not_marked_live_is_rejected(make_client, stream_type):
    transport = live_transport([twitch_stream(stream_type=stream_type)])

    with pytest.raises(TwitchAPIError, match="not marked live"):
        lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(transport))


@pytest.mark.parametrize(
    "started_at",
    ["", "not-a-date", "2026-09-04", "2026-09-04T09:00:00", None, 12345],
)
def test_unusable_start_time_is_rejected(make_client, started_at):
    """A live payload we cannot date is unusable; the local clock is never substituted."""
    transport = live_transport([twitch_stream(started_at=started_at)])

    with pytest.raises(TwitchAPIError):
        lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(transport))


@pytest.mark.parametrize("viewer_count", ["many", None, -1, True])
def test_unusable_viewer_count_is_rejected(make_client, viewer_count):
    transport = live_transport([twitch_stream(viewer_count=viewer_count)])

    with pytest.raises(TwitchAPIError):
        lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(transport))


def test_offset_start_times_are_accepted(make_client):
    transport = live_transport([twitch_stream(started_at="2026-09-04T11:00:00+02:00")])

    stream = lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(transport))

    assert stream.started_at == datetime(2026, 9, 4, 9, 0, tzinfo=UTC)


# -- failures ----------------------------------------------------------------


def test_server_error_raises_rather_than_reporting_offline(make_client):
    transport = failing_transport(json_response(500, {"message": "Internal Server Error"}))

    with pytest.raises(TwitchAPIError) as exc_info:
        lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(transport))

    assert exc_info.value.status_code == 500


def test_transport_failure_raises_rather_than_reporting_offline(make_client):
    def explode(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(TwitchAPIError, match="Could not reach Twitch"):
        lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(failing_transport(explode)))


def test_rejected_app_token_is_reminted_and_retried_once(make_client):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: [
                json_response(
                    200, token_payload(access_token="STALE-APP-TOKEN", refresh_token=None)
                ),
                json_response(200, token_payload(access_token=FAKE_APP_TOKEN, refresh_token=None)),
            ],
            HELIX_STREAMS_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(200, {"data": [twitch_stream()]}),
            ],
        }
    )

    stream = lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(transport))

    assert stream is not None
    assert transport.call_count == 4, "token, rejected call, new token, retried call"


def test_a_second_rejection_is_not_retried_again(make_client):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: [
                json_response(200, token_payload(access_token="A", refresh_token=None)),
                json_response(200, token_payload(access_token="B", refresh_token=None)),
            ],
            HELIX_STREAMS_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(401, {"message": "Invalid OAuth token"}),
            ],
        }
    )

    with pytest.raises(TwitchAuthenticationError):
        lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(transport))

    assert transport.call_count == 4, "the cycle must not loop"


def test_no_token_or_secret_appears_in_errors(make_client):
    transport = failing_transport(json_response(500, {"message": "Internal Server Error"}))

    with pytest.raises(TwitchAPIError) as exc_info:
        lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(transport))

    message = str(exc_info.value)
    assert FAKE_APP_TOKEN not in message
    assert FAKE_CLIENT_SECRET not in message


def test_the_app_token_never_travels_in_a_url(make_client):
    transport = live_transport()

    lookup_stream_by_user_id(TWITCH_USER_ID, client=make_client(transport))

    for request in transport.requests:
        assert FAKE_APP_TOKEN not in str(request.url)
        assert FAKE_CLIENT_SECRET not in str(request.url)
