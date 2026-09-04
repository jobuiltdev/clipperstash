"""Cross-cutting checks that secrets cannot escape the integration.

Every fake credential used by the suite is deliberately distinctive, so any of
these assertions failing means a real leak path exists.
"""

from __future__ import annotations

import logging

import pytest
from django.urls import reverse

from apps.twitch import services
from apps.twitch.client import OAUTH_TOKEN_URL, OAUTH_VALIDATE_URL
from apps.twitch.exceptions import TwitchAuthenticationError
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

SENSITIVE_VALUES = (*ALL_FAKE_SECRETS, FAKE_CLIENT_SECRET)

pytestmark = pytest.mark.django_db


def test_no_public_endpoint_leaks_a_secret(client, connection):
    surfaces = [
        client.get(reverse("health")),
        client.get(reverse("twitch:connection")),
        client.get(reverse("twitch:oauth-start")),
    ]

    for response in surfaces:
        text = response.content.decode() + response.get("Location", "")
        for secret in SENSITIVE_VALUES:
            assert secret not in text


def test_full_oauth_round_trip_logs_no_secret(client, patch_service_client, caplog):
    from apps.twitch import oauth

    patch_service_client(
        routed_transport(
            {
                OAUTH_TOKEN_URL: json_response(200, token_payload()),
                HELIX_USERS_URL: json_response(200, users_payload()),
            }
        )
    )

    with caplog.at_level(logging.DEBUG):
        client.get(
            reverse("twitch:oauth-callback"),
            {"code": FAKE_AUTH_CODE, "state": oauth.create_state()},
        )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in SENSITIVE_VALUES:
        assert secret not in logged


def test_refresh_failure_logs_no_secret(make_client, expired_connection, caplog):
    transport = routed_transport(
        {OAUTH_TOKEN_URL: json_response(400, {"message": "Invalid refresh token"})}
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(TwitchAuthenticationError):
        services.refresh_connection(expired_connection, client=make_client(transport))

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert FAKE_REFRESH_TOKEN not in logged
    assert FAKE_CLIENT_SECRET not in logged


def test_connection_status_payload_is_a_strict_allowlist(connection):
    payload = services.build_connection_status(connection)

    assert set(payload) == {"connected", "account", "scopes", "requires_reauthorization"}
    assert set(payload["account"]) == {"id", "login", "display_name"}


def test_no_model_field_named_like_a_secret_is_published(connection):
    payload = services.build_connection_status(connection)
    flattened = f"{payload}"

    assert FAKE_ACCESS_TOKEN not in flattened
    assert FAKE_REFRESH_TOKEN not in flattened


def test_validation_never_places_the_token_in_a_url(make_client, connection):
    transport = routed_transport(
        {OAUTH_VALIDATE_URL: json_response(200, {"user_id": "1", "login": "a", "expires_in": 1})}
    )

    services.validate_connection(connection, client=make_client(transport))

    assert FAKE_ACCESS_TOKEN not in str(transport.request_for(OAUTH_VALIDATE_URL).url)


def test_token_exchange_never_places_the_secret_in_a_url(make_client, connection):
    transport = routed_transport({OAUTH_TOKEN_URL: json_response(200, token_payload())})

    make_client(transport).exchange_authorization_code(FAKE_AUTH_CODE)

    url = str(transport.request_for(OAUTH_TOKEN_URL).url)
    assert FAKE_CLIENT_SECRET not in url
    assert FAKE_AUTH_CODE not in url


def test_settings_never_expose_the_secret_to_the_frontend(settings):
    """Nothing prefixed for the browser may carry backend credentials."""
    for name in dir(settings):
        if name.startswith("NEXT_PUBLIC"):
            raise AssertionError(f"Backend settings must not define {name}")
    assert settings.TWITCH_CLIENT_SECRET == FAKE_CLIENT_SECRET
