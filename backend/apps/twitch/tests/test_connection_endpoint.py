"""Tests for `GET /api/twitch/connection/` and the model's secret hygiene."""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from apps.twitch.models import TwitchConnection
from apps.twitch.tests.conftest import ALL_FAKE_SECRETS, FAKE_ACCESS_TOKEN, FAKE_REFRESH_TOKEN
from conftest import FAKE_CLIENT_SECRET

pytestmark = pytest.mark.django_db


def status_url() -> str:
    return reverse("twitch:connection")


def test_reports_disconnected_when_no_connection_exists(client):
    response = client.get(status_url())

    assert response.status_code == 200
    assert response.json() == {
        "connected": False,
        "account": None,
        "scopes": [],
        "requires_reauthorization": False,
        "capabilities": {"chat_read": False},
    }


def test_reports_the_connected_account(client, connection):
    response = client.get(status_url())

    assert response.status_code == 200
    assert response.json() == {
        "connected": True,
        "account": {"id": "123456", "login": "example", "display_name": "Example"},
        "scopes": ["clips:edit", "user:read:chat"],
        "requires_reauthorization": False,
        "capabilities": {"chat_read": True},
    }


def test_reports_when_reauthorization_is_required(client, connection):
    connection.mark_requires_reauthorization()

    payload = client.get(status_url()).json()

    assert payload["connected"] is True
    assert payload["requires_reauthorization"] is True


def test_response_never_contains_token_material(client, connection):
    body = client.get(status_url()).content.decode()

    for secret in ALL_FAKE_SECRETS:
        assert secret not in body
    assert FAKE_CLIENT_SECRET not in body


def test_response_exposes_no_token_shaped_keys(client, connection):
    payload = client.get(status_url()).json()

    serialized = json.dumps(payload).lower()
    for forbidden in ("access_token", "refresh_token", "client_secret", "authorization:"):
        assert forbidden not in serialized


def test_model_string_representations_hide_tokens(connection):
    assert FAKE_ACCESS_TOKEN not in str(connection)
    assert FAKE_REFRESH_TOKEN not in str(connection)
    assert FAKE_ACCESS_TOKEN not in repr(connection)
    assert FAKE_REFRESH_TOKEN not in repr(connection)


def test_current_returns_the_most_recently_updated_connection(connection):
    other = TwitchConnection.objects.create(
        twitch_user_id="999",
        login="second",
        display_name="Second",
        access_token="x",
        refresh_token="y",
        token_expires_at=connection.token_expires_at,
        scopes=[],
    )

    assert TwitchConnection.objects.current() == other


def test_current_is_none_when_nothing_is_connected(db):
    assert TwitchConnection.objects.current() is None


def test_a_connection_without_the_chat_scope_needs_reauthorization(client, connection):
    """An older connection stays valid, but is reported as not chat-capable."""
    connection.scopes = ["clips:edit"]
    connection.save(update_fields=["scopes"])

    payload = client.get(status_url()).json()

    assert payload["connected"] is True
    assert payload["scopes"] == ["clips:edit"]
    assert payload["requires_reauthorization"] is True
    assert payload["capabilities"]["chat_read"] is False


def test_a_connection_with_both_scopes_is_chat_capable(client, connection):
    payload = client.get(status_url()).json()

    assert payload["requires_reauthorization"] is False
    assert payload["capabilities"]["chat_read"] is True


def test_a_rejected_connection_is_not_chat_capable(client, connection):
    connection.mark_requires_reauthorization()

    payload = client.get(status_url()).json()

    assert payload["capabilities"]["chat_read"] is False
    assert payload["requires_reauthorization"] is True


def test_capability_reporting_never_exposes_tokens(client, connection):
    connection.scopes = ["clips:edit"]
    connection.save(update_fields=["scopes"])

    body = client.get(status_url()).content.decode()

    for secret in ALL_FAKE_SECRETS:
        assert secret not in body
