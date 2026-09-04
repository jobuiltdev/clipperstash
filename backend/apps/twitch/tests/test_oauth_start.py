"""Tests for `GET /api/twitch/oauth/start/` and the state machinery."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.twitch import oauth
from apps.twitch.client import OAUTH_AUTHORIZE_URL, TwitchCredentials
from apps.twitch.exceptions import TwitchOAuthStateError
from conftest import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REDIRECT_URI


def authorization_query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def test_start_redirects_to_twitch(client):
    response = client.get(reverse("twitch:oauth-start"))

    assert response.status_code == 302
    assert response["Location"].startswith(OAUTH_AUTHORIZE_URL)


def test_start_requests_only_the_clips_edit_scope(client):
    response = client.get(reverse("twitch:oauth-start"))

    scopes = authorization_query(response["Location"])["scope"][0].split(" ")
    assert scopes == ["clips:edit"]


def test_start_does_not_request_unrelated_scopes(client):
    response = client.get(reverse("twitch:oauth-start"))

    scope = authorization_query(response["Location"])["scope"][0]
    for unrelated in ("user:read:email", "channel:read:subscriptions", "chat:read", "moderator"):
        assert unrelated not in scope


def test_start_uses_the_authorization_code_response_type(client):
    response = client.get(reverse("twitch:oauth-start"))

    query = authorization_query(response["Location"])
    assert query["response_type"] == ["code"]
    assert "token" not in query.get("response_type", [])


def test_start_uses_the_configured_client_id_and_redirect_uri(client):
    response = client.get(reverse("twitch:oauth-start"))

    query = authorization_query(response["Location"])
    assert query["client_id"] == [FAKE_CLIENT_ID]
    assert query["redirect_uri"] == [FAKE_REDIRECT_URI]


def test_start_never_exposes_the_client_secret(client):
    response = client.get(reverse("twitch:oauth-start"))

    assert FAKE_CLIENT_SECRET not in response["Location"]
    assert FAKE_CLIENT_SECRET not in response.content.decode()


def test_start_stores_the_state_server_side(client):
    response = client.get(reverse("twitch:oauth-start"))

    state = authorization_query(response["Location"])["state"][0]
    assert cache.get(f"{oauth.STATE_CACHE_PREFIX}{state}") is True


def test_start_reports_a_configuration_failure_without_leaking_settings(client, settings):
    settings.TWITCH_CLIENT_ID = ""

    response = client.get(reverse("twitch:oauth-start"))

    assert response.status_code == 503
    body = response.content.decode()
    assert "TWITCH_CLIENT_ID" in body
    assert FAKE_CLIENT_SECRET not in body


def test_state_values_are_unpredictable_and_unique():
    states = {oauth.create_state() for _ in range(25)}

    assert len(states) == 25
    assert all(len(state) >= 32 for state in states)


def test_state_can_be_consumed_once():
    state = oauth.create_state()

    oauth.consume_state(state)

    with pytest.raises(TwitchOAuthStateError):
        oauth.consume_state(state)


def test_missing_state_is_rejected():
    with pytest.raises(TwitchOAuthStateError, match="missing"):
        oauth.consume_state(None)

    with pytest.raises(TwitchOAuthStateError, match="missing"):
        oauth.consume_state("")


def test_unknown_state_is_rejected():
    with pytest.raises(TwitchOAuthStateError, match="unknown"):
        oauth.consume_state("a-state-this-application-never-issued")


def test_expired_state_is_rejected():
    state = oauth.create_state()
    # An expired entry is indistinguishable from an absent one to the cache.
    cache.delete(f"{oauth.STATE_CACHE_PREFIX}{state}")

    with pytest.raises(TwitchOAuthStateError):
        oauth.consume_state(state)


def test_authorization_url_carries_no_secret():
    url = oauth.build_authorization_url(TwitchCredentials.from_settings(), state="abc")

    assert FAKE_CLIENT_SECRET not in url
    assert "client_secret" not in url
