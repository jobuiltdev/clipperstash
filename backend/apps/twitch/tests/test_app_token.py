"""Tests for the cached app access token (Client Credentials grant)."""

from __future__ import annotations

import pytest
from django.core.cache import cache

from apps.twitch.client import OAUTH_TOKEN_URL
from apps.twitch.exceptions import TwitchAPIError, TwitchAuthenticationError
from apps.twitch.services import (
    APP_TOKEN_CACHE_KEY,
    APP_TOKEN_SAFETY_MARGIN_SECONDS,
    clear_app_access_token,
    get_app_access_token,
)
from apps.twitch.tests.conftest import (
    FAKE_APP_TOKEN,
    json_response,
    routed_transport,
    token_payload,
)
from conftest import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET


def test_requests_a_token_when_the_cache_is_cold(make_client):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: json_response(
                200, token_payload(access_token=FAKE_APP_TOKEN, refresh_token=None)
            )
        }
    )

    token = get_app_access_token(client=make_client(transport))

    assert token == FAKE_APP_TOKEN
    assert transport.call_count == 1


def test_uses_the_client_credentials_grant(make_client):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: json_response(
                200, token_payload(access_token=FAKE_APP_TOKEN, refresh_token=None)
            )
        }
    )

    get_app_access_token(client=make_client(transport))

    body = transport.request_for(OAUTH_TOKEN_URL).content.decode()
    assert "grant_type=client_credentials" in body
    assert FAKE_CLIENT_ID in body
    # The secret travels in the POST body, never in the URL.
    assert FAKE_CLIENT_SECRET not in str(transport.request_for(OAUTH_TOKEN_URL).url)


def test_reuses_the_cached_token(make_client):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: [
                json_response(200, token_payload(access_token=FAKE_APP_TOKEN, refresh_token=None))
            ]
        }
    )
    client = make_client(transport)

    first = get_app_access_token(client=client)
    second = get_app_access_token(client=client)

    assert first == second == FAKE_APP_TOKEN
    assert transport.call_count == 1, "the second call must be served from the cache"


def test_cache_ttl_applies_the_safety_margin(make_client, recording_cache):
    expires_in = 3600
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: json_response(
                200,
                token_payload(
                    access_token=FAKE_APP_TOKEN, refresh_token=None, expires_in=expires_in
                ),
            )
        }
    )

    get_app_access_token(client=make_client(transport))

    ttl = recording_cache.timeout_for(APP_TOKEN_CACHE_KEY)
    assert ttl == expires_in - APP_TOKEN_SAFETY_MARGIN_SECONDS
    assert ttl < expires_in, "an almost-expired token must not be reused"


def test_token_shorter_than_the_safety_margin_is_not_cached(make_client, recording_cache):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: json_response(
                200,
                token_payload(
                    access_token=FAKE_APP_TOKEN,
                    refresh_token=None,
                    expires_in=APP_TOKEN_SAFETY_MARGIN_SECONDS - 1,
                ),
            )
        }
    )

    token = get_app_access_token(client=make_client(transport))

    assert token == FAKE_APP_TOKEN
    assert not recording_cache.was_set(APP_TOKEN_CACHE_KEY)


def test_force_refresh_bypasses_the_cache(make_client):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: [
                json_response(
                    200, token_payload(access_token="FIRST-APP-TOKEN", refresh_token=None)
                ),
                json_response(
                    200, token_payload(access_token="SECOND-APP-TOKEN", refresh_token=None)
                ),
            ]
        }
    )
    client = make_client(transport)

    assert get_app_access_token(client=client) == "FIRST-APP-TOKEN"
    assert get_app_access_token(client=client, force_refresh=True) == "SECOND-APP-TOKEN"
    assert transport.call_count == 2


def test_clear_app_access_token_empties_the_cache(make_client):
    transport = routed_transport(
        {
            OAUTH_TOKEN_URL: [
                json_response(200, token_payload(access_token=FAKE_APP_TOKEN, refresh_token=None)),
                json_response(200, token_payload(access_token=FAKE_APP_TOKEN, refresh_token=None)),
            ]
        }
    )
    client = make_client(transport)

    get_app_access_token(client=client)
    clear_app_access_token()
    get_app_access_token(client=client)

    assert transport.call_count == 2


def test_rejected_credentials_raise_an_authentication_error(make_client):
    transport = routed_transport(
        {OAUTH_TOKEN_URL: json_response(401, {"message": "invalid client secret"})}
    )

    with pytest.raises(TwitchAuthenticationError):
        get_app_access_token(client=make_client(transport))

    assert cache.get(APP_TOKEN_CACHE_KEY) is None


def test_oauth_server_error_raises_an_api_error(make_client):
    transport = routed_transport(
        {OAUTH_TOKEN_URL: json_response(503, {"message": "service unavailable"})}
    )

    with pytest.raises(TwitchAPIError) as exc_info:
        get_app_access_token(client=make_client(transport))

    assert exc_info.value.status_code == 503
    assert FAKE_CLIENT_SECRET not in str(exc_info.value)
