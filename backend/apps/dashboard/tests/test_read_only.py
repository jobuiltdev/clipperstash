"""The guarantees that make this a dashboard rather than a control panel.

Milestone 7 is observation only. Every endpoint added here must be incapable of
changing what it describes, of reaching Twitch, of running the detector, and of
publishing chat or credentials. Those are properties of the whole surface, so
they are asserted against every route at once — a route added later without
them will fail here rather than quietly ship.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from django.urls import reverse
from django.utils import timezone

from apps.clips.models import Clip
from apps.moments.models import MomentCandidate
from apps.monitoring.models import ChatMessage, StreamSession
from apps.twitch.models import TwitchConnection

from .conftest import CHAT_TEXT, CHATTER_HASH

pytestmark = pytest.mark.django_db

FAKE_ACCESS_TOKEN = "FAKE-ACCESS-TOKEN-do-not-leak-1a2b3c"
FAKE_REFRESH_TOKEN = "FAKE-REFRESH-TOKEN-do-not-leak-4d5e6f"

WRITE_METHODS = ("post", "put", "patch", "delete")


def all_urls(fixtures) -> list[str]:
    """Every route this milestone adds, with real ids behind them."""
    return [
        reverse("dashboard:overview"),
        reverse("dashboard:detector-config"),
        reverse("streamers:sessions", args=[fixtures.streamer.pk]),
        reverse("dashboard:session-detail", args=[fixtures.live_session.pk]),
        reverse("dashboard:session-moments", args=[fixtures.live_session.pk]),
        reverse("dashboard:moment-detail", args=[fixtures.created.pk]),
    ]


def snapshot() -> dict:
    """Everything a dashboard request could conceivably disturb."""
    return {
        "moments": list(
            MomentCandidate.objects.order_by("pk").values_list("pk", "status", "updated_at")
        ),
        "clips": list(
            Clip.objects.order_by("pk").values_list(
                "pk", "twitch_clip_id", "failure_code", "updated_at"
            )
        ),
        "sessions": list(
            StreamSession.objects.order_by("pk").values_list("pk", "status", "updated_at")
        ),
        "chat_messages": ChatMessage.objects.count(),
    }


# -- no writes ---------------------------------------------------------------


def test_every_route_refuses_every_write_method(client, populated):
    for url in all_urls(populated):
        for method in WRITE_METHODS:
            response = getattr(client, method)(url)
            assert response.status_code == 405, f"{method.upper()} {url}"


def test_reading_the_whole_surface_changes_nothing(client, populated):
    before = snapshot()

    for url in all_urls(populated):
        assert client.get(url).status_code == 200

    assert snapshot() == before


def test_reading_a_moment_does_not_advance_its_lifecycle(client, populated):
    """Looking at a moment awaiting a clip must not request one."""
    url = reverse("dashboard:moment-detail", args=[populated.unknown.pk])
    before = populated.unknown.status

    client.get(url)

    populated.unknown.refresh_from_db()
    assert populated.unknown.status == before
    assert populated.unknown.clip.twitch_clip_id is None


# -- nothing external --------------------------------------------------------


def test_no_route_makes_an_http_request(client, populated, monkeypatch):
    """Not just "no Twitch call" — no outbound HTTP at all."""

    def refuse(*args, **kwargs):
        raise AssertionError("The dashboard must never make an HTTP request.")

    monkeypatch.setattr(httpx.Client, "send", refuse)

    for url in all_urls(populated):
        assert client.get(url).status_code == 200


def test_no_route_runs_the_detector(client, populated, monkeypatch):
    import apps.moments.detector.detector as detector_module

    def refuse(*args, **kwargs):
        raise AssertionError("The dashboard must never run the detector.")

    monkeypatch.setattr(detector_module, "score_samples", refuse)

    for url in all_urls(populated):
        assert client.get(url).status_code == 200


def test_no_route_requests_a_clip(client, populated, monkeypatch):
    import apps.clips.services as clip_services

    def refuse(*args, **kwargs):
        raise AssertionError("The dashboard must never request a clip.")

    monkeypatch.setattr(clip_services, "request_clip", refuse)

    for url in all_urls(populated):
        assert client.get(url).status_code == 200


# -- nothing private ---------------------------------------------------------


def test_no_route_publishes_chat_text_or_chatter_identity(client, populated):
    for url in all_urls(populated):
        body = client.get(url).content.decode()
        assert CHAT_TEXT not in body, url
        assert CHATTER_HASH not in body, url


def test_no_route_publishes_twitch_message_identifiers(client, populated):
    message = ChatMessage.objects.first()

    for url in all_urls(populated):
        body = client.get(url).content.decode()
        assert message.twitch_event_message_id not in body, url
        assert message.twitch_message_id not in body, url


def test_no_route_publishes_a_token_or_the_client_secret(client, populated, settings):
    TwitchConnection.objects.create(
        twitch_user_id="123456",
        login="example",
        display_name="Example",
        access_token=FAKE_ACCESS_TOKEN,
        refresh_token=FAKE_REFRESH_TOKEN,
        token_expires_at=timezone.now() + timedelta(hours=4),
        scopes=["clips:edit", "user:read:chat"],
    )

    for url in all_urls(populated):
        body = client.get(url).content.decode()
        assert FAKE_ACCESS_TOKEN not in body, url
        assert FAKE_REFRESH_TOKEN not in body, url
        assert settings.TWITCH_CLIENT_SECRET not in body, url


def test_serialized_moments_expose_no_unexpected_field(client, populated):
    """The serializers are allowlists; this is what the allowlist admits."""
    moment = client.get(reverse("dashboard:moment-detail", args=[populated.created.pk])).json()[
        "moment"
    ]

    assert set(moment) == {
        "id",
        "detected_at",
        "status",
        "total_score",
        "velocity_score",
        "reaction_score",
        "emote_score",
        "diversity_score",
        "absolute_activity_score",
        "velocity_ratio",
        "current_message_count",
        "baseline_message_count",
        "current_unique_chatter_count",
        "baseline_unique_chatter_count",
        "current_emote_count",
        "baseline_emote_count",
        "current_reaction_count",
        "baseline_reaction_count",
        "clip_state",
        "clip_id",
        "twitch_clip_id",
        "twitch_url",
        "failure_code",
        "requested_at",
        "ready_at",
        "session_id",
        "streamer",
        "windows",
        "threshold",
        "failure_detail",
        "clip",
    }


def test_a_serialized_clip_carries_no_edit_url(client, populated):
    """Twitch's `edit_url` is a capability, and was never stored to begin with."""
    moment = client.get(reverse("dashboard:moment-detail", args=[populated.created.pk])).json()[
        "moment"
    ]

    assert "edit_url" not in moment["clip"]
    assert "edit" not in moment["clip"]["twitch_url"]
