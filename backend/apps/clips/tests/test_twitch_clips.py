"""Tests for the Twitch Create Clip and Get Clips transport."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from apps.twitch.client import OAUTH_TOKEN_URL
from apps.twitch.exceptions import TwitchAPIError, TwitchAuthenticationError
from apps.twitch.services import create_clip, get_clip
from apps.twitch.tests.conftest import (
    FAKE_ACCESS_TOKEN,
    FAKE_ACCESS_TOKEN_2,
    FAKE_APP_TOKEN,
    json_response,
    routed_transport,
    token_payload,
)
from conftest import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET

from .conftest import (
    BROADCASTER_ID,
    CLIP_URL,
    EMPTY_CLIPS,
    HELIX_CLIPS_URL,
    TWITCH_CLIP_ID,
    accepted_payload,
    clip_payload,
    clip_transport,
)

pytestmark = pytest.mark.django_db


# =========================================================== create clip =====


def test_the_request_targets_the_clips_endpoint(connection, make_client):
    transport = clip_transport()

    create_clip(connection, broadcaster_id=BROADCASTER_ID, client=make_client(transport))

    request = transport.request_for(HELIX_CLIPS_URL)
    assert request.method == "POST"
    assert str(request.url).split("?")[0] == "https://api.twitch.tv/helix/clips"


def test_the_broadcaster_is_the_streamers_twitch_id(connection, make_client):
    transport = clip_transport()

    create_clip(connection, broadcaster_id=BROADCASTER_ID, client=make_client(transport))

    request = transport.request_for(HELIX_CLIPS_URL)
    assert request.url.params["broadcaster_id"] == BROADCASTER_ID
    assert "shroud" not in str(request.url), "a login is never used as identity"


def test_the_connected_user_token_is_used_not_the_app_token(connection, make_client):
    transport = clip_transport()

    create_clip(connection, broadcaster_id=BROADCASTER_ID, client=make_client(transport))

    request = transport.request_for(HELIX_CLIPS_URL)
    assert request.headers["Authorization"] == f"Bearer {FAKE_ACCESS_TOKEN}"
    assert request.headers["Client-Id"] == FAKE_CLIENT_ID
    assert FAKE_APP_TOKEN not in request.headers["Authorization"]

    called = {str(sent.url).split("?")[0] for sent in transport.requests}
    assert OAUTH_TOKEN_URL not in called, "no app token may be minted for clip creation"


def test_no_title_or_duration_is_sent(connection, make_client):
    """Twitch's defaults are used: a custom title risks AutoMod rejection."""
    transport = clip_transport()

    create_clip(connection, broadcaster_id=BROADCASTER_ID, client=make_client(transport))

    request = transport.request_for(HELIX_CLIPS_URL)
    assert set(request.url.params) == {"broadcaster_id"}
    assert request.content == b""


def test_the_accepted_response_is_parsed(connection, make_client):
    requested = create_clip(
        connection, broadcaster_id=BROADCASTER_ID, client=make_client(clip_transport())
    )

    assert requested.clip_id == TWITCH_CLIP_ID


def test_the_edit_url_is_not_carried_into_the_domain(connection, make_client):
    """Twitch supplies it; ClipperStash validates the shape and drops it."""
    requested = create_clip(
        connection, broadcaster_id=BROADCASTER_ID, client=make_client(clip_transport())
    )

    assert not hasattr(requested, "edit_url")
    assert "edit" not in repr(requested)


@pytest.mark.parametrize(
    "payload",
    [{"data": []}, {"data": "nope"}, {}, {"data": [{"edit_url": "x"}]}, {"data": [{"id": ""}]}],
)
def test_a_malformed_acceptance_is_rejected(connection, make_client, payload):
    transport = clip_transport(create=json_response(202, payload))

    with pytest.raises(TwitchAPIError):
        create_clip(connection, broadcaster_id=BROADCASTER_ID, client=make_client(transport))


@pytest.mark.parametrize("status", [200, 204])
def test_a_success_that_is_not_202_is_rejected(connection, make_client, status):
    """Only 202 means the request was accepted."""
    transport = clip_transport(create=json_response(status, accepted_payload()))

    with pytest.raises(TwitchAPIError, match="did not accept"):
        create_clip(connection, broadcaster_id=BROADCASTER_ID, client=make_client(transport))


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (400, "invalid broadcaster"),
        (403, "clips are disabled for this channel"),
        (404, "the broadcaster is not live"),
        (500, "Internal Server Error"),
        (503, "service unavailable"),
    ],
)
def test_error_responses_map_to_typed_api_errors(connection, make_client, status, message):
    transport = clip_transport(create=json_response(status, {"message": message}))

    with pytest.raises(TwitchAPIError) as exc_info:
        create_clip(connection, broadcaster_id=BROADCASTER_ID, client=make_client(transport))

    assert exc_info.value.status_code == status


def test_a_forbidden_response_is_not_an_authentication_failure(connection, make_client):
    """403 means the channel disallows clips, which re-authorizing cannot fix."""
    transport = clip_transport(create=json_response(403, {"message": "clips disabled"}))

    with pytest.raises(TwitchAPIError) as exc_info:
        create_clip(connection, broadcaster_id=BROADCASTER_ID, client=make_client(transport))

    assert not isinstance(exc_info.value, TwitchAuthenticationError)
    connection.refresh_from_db()
    assert connection.requires_reauthorization is False


def test_a_network_failure_is_mapped_safely(connection, make_client):
    def explode(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    transport = clip_transport(create=explode)

    with pytest.raises(TwitchAPIError, match="Could not reach Twitch"):
        create_clip(connection, broadcaster_id=BROADCASTER_ID, client=make_client(transport))


def test_a_rejected_token_refreshes_once_and_retries(connection, make_client):
    transport = routed_transport(
        {
            HELIX_CLIPS_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(202, accepted_payload()),
            ],
            OAUTH_TOKEN_URL: json_response(
                200, token_payload(access_token=FAKE_ACCESS_TOKEN_2, refresh_token=None)
            ),
        }
    )

    requested = create_clip(
        connection, broadcaster_id=BROADCASTER_ID, client=make_client(transport)
    )

    assert requested.clip_id == TWITCH_CLIP_ID
    assert transport.call_count == 3, "rejected call, one refresh, one retry"

    connection.refresh_from_db()
    assert connection.get_access_token() == FAKE_ACCESS_TOKEN_2
    assert connection.requires_reauthorization is False


def test_a_second_rejection_stops_and_flags_the_connection(connection, make_client):
    transport = routed_transport(
        {
            HELIX_CLIPS_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(401, {"message": "Invalid OAuth token"}),
            ],
            OAUTH_TOKEN_URL: json_response(200, token_payload()),
        }
    )

    with pytest.raises(TwitchAuthenticationError):
        create_clip(connection, broadcaster_id=BROADCASTER_ID, client=make_client(transport))

    assert transport.call_count == 3, "the cycle must not loop"
    connection.refresh_from_db()
    assert connection.requires_reauthorization is True


def test_no_secret_appears_in_create_errors(connection, make_client):
    transport = clip_transport(create=json_response(500, {"message": "Internal Server Error"}))

    with pytest.raises(TwitchAPIError) as exc_info:
        create_clip(connection, broadcaster_id=BROADCASTER_ID, client=make_client(transport))

    message = str(exc_info.value)
    assert FAKE_ACCESS_TOKEN not in message
    assert FAKE_CLIENT_SECRET not in message


def test_no_token_travels_in_the_clip_request_url(connection, make_client):
    transport = clip_transport()

    create_clip(connection, broadcaster_id=BROADCASTER_ID, client=make_client(transport))

    for request in transport.requests:
        assert FAKE_ACCESS_TOKEN not in str(request.url)
        assert FAKE_CLIENT_SECRET not in str(request.url)


# ============================================================= get clips =====


def lookup_transport(*responses):
    return routed_transport(
        {
            HELIX_CLIPS_URL: list(responses) if len(responses) > 1 else responses[0],
            OAUTH_TOKEN_URL: json_response(200, token_payload()),
        }
    )


def test_the_lookup_sends_the_clip_id(connection, make_client):
    transport = lookup_transport(json_response(200, clip_payload()))

    get_clip(connection, TWITCH_CLIP_ID, client=make_client(transport))

    request = transport.request_for(HELIX_CLIPS_URL)
    assert request.method == "GET"
    assert request.url.params["id"] == TWITCH_CLIP_ID


def test_the_lookup_uses_the_connected_user_token(connection, make_client):
    """Documented choice: the same credentials that requested the clip verify it."""
    transport = lookup_transport(json_response(200, clip_payload()))

    get_clip(connection, TWITCH_CLIP_ID, client=make_client(transport))

    request = transport.request_for(HELIX_CLIPS_URL)
    assert request.headers["Authorization"] == f"Bearer {FAKE_ACCESS_TOKEN}"
    called = {str(sent.url).split("?")[0] for sent in transport.requests}
    assert OAUTH_TOKEN_URL not in called


def test_an_empty_result_means_not_ready_rather_than_failed(connection, make_client):
    transport = lookup_transport(json_response(200, EMPTY_CLIPS))

    assert get_clip(connection, TWITCH_CLIP_ID, client=make_client(transport)) is None


def test_a_returned_clip_is_parsed(connection, make_client):
    transport = lookup_transport(json_response(200, clip_payload()))

    clip = get_clip(connection, TWITCH_CLIP_ID, client=make_client(transport))

    assert clip.clip_id == TWITCH_CLIP_ID
    assert clip.url == CLIP_URL
    assert clip.broadcaster_id == BROADCASTER_ID
    assert clip.creator_id == "123456"
    assert clip.video_id == "1234567890"
    assert clip.game_id == "509658"
    assert clip.language == "en"
    assert clip.title == "insane play"
    assert clip.duration == 28.5
    assert clip.thumbnail_url.endswith("-preview.jpg")
    assert clip.created_at == datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    assert clip.created_at.tzinfo is not None


def test_the_url_comes_from_twitch_not_from_the_id(connection, make_client):
    transport = lookup_transport(
        json_response(200, clip_payload(url="https://clips.twitch.tv/somewhere-else"))
    )

    clip = get_clip(connection, TWITCH_CLIP_ID, client=make_client(transport))

    assert clip.url == "https://clips.twitch.tv/somewhere-else"


@pytest.mark.parametrize(
    "entry",
    [
        "not-an-object",
        {"url": CLIP_URL, "broadcaster_id": BROADCASTER_ID},
        {"id": TWITCH_CLIP_ID, "broadcaster_id": BROADCASTER_ID},
        {"id": TWITCH_CLIP_ID, "url": CLIP_URL},
        {"id": TWITCH_CLIP_ID, "url": CLIP_URL, "broadcaster_id": BROADCASTER_ID},
    ],
)
def test_a_malformed_clip_entry_is_rejected(connection, make_client, entry):
    transport = lookup_transport(json_response(200, {"data": [entry]}))

    with pytest.raises(TwitchAPIError):
        get_clip(connection, TWITCH_CLIP_ID, client=make_client(transport))


@pytest.mark.parametrize("payload", [{"data": "nope"}, {}])
def test_a_malformed_clips_payload_is_rejected(connection, make_client, payload):
    transport = lookup_transport(json_response(200, payload))

    with pytest.raises(TwitchAPIError):
        get_clip(connection, TWITCH_CLIP_ID, client=make_client(transport))


@pytest.mark.parametrize("created_at", ["", "not-a-date", "2026-09-05T12:00:00", None])
def test_an_unusable_creation_time_is_rejected(connection, make_client, created_at):
    entry = clip_payload()["data"][0]
    entry["created_at"] = created_at
    transport = lookup_transport(json_response(200, {"data": [entry]}))

    with pytest.raises(TwitchAPIError):
        get_clip(connection, TWITCH_CLIP_ID, client=make_client(transport))


@pytest.mark.parametrize("duration", ["long", None, -1, True])
def test_an_unusable_duration_is_rejected(connection, make_client, duration):
    transport = lookup_transport(json_response(200, clip_payload(duration=duration)))

    with pytest.raises(TwitchAPIError):
        get_clip(connection, TWITCH_CLIP_ID, client=make_client(transport))
