"""Tests for streamer persistence and idempotency."""

from __future__ import annotations

import pytest

from apps.streamers.exceptions import StreamerConflictError, StreamerNotFoundError
from apps.streamers.models import Platform, Streamer
from apps.streamers.services import upsert_twitch_streamer
from apps.twitch.client import TwitchIdentity

from .conftest import HELIX_USERS_URL, lookup_transport, twitch_user

pytestmark = pytest.mark.django_db


def identity(**overrides) -> TwitchIdentity:
    values = {
        "user_id": "37402112",
        "login": "shroud",
        "display_name": "shroud",
        "profile_image_url": "https://static-cdn.example/shroud-profile.png",
        "broadcaster_type": "partner",
        "description": "Professional gamer.",
    }
    values.update(overrides)
    return TwitchIdentity(**values)


# -- creation ----------------------------------------------------------------


def test_resolution_creates_a_streamer(resolve_with):
    streamer = resolve_with(lookup_transport(), "https://www.twitch.tv/shroud")

    assert Streamer.objects.count() == 1
    assert streamer.platform == Platform.TWITCH
    assert streamer.platform_user_id == "37402112"
    assert streamer.username == "shroud"
    assert streamer.display_name == "shroud"
    assert streamer.channel_url == "https://www.twitch.tv/shroud"
    assert streamer.profile_image_url == "https://static-cdn.example/shroud-profile.png"
    assert streamer.broadcaster_type == "partner"
    assert streamer.description == "Professional gamer."
    assert streamer.is_active is True


def test_username_is_persisted_normalized(resolve_with):
    streamer = resolve_with(
        lookup_transport(users=[twitch_user(login="shroud", display_name="Shroud")]),
        "https://www.twitch.tv/SHROUD",
    )

    assert streamer.username == "shroud"
    assert streamer.display_name == "Shroud", "display name keeps Twitch's own casing"


def test_canonical_channel_url_is_persisted(resolve_with):
    streamer = resolve_with(lookup_transport(), "twitch.tv/shroud")

    assert streamer.channel_url == "https://www.twitch.tv/shroud"


def test_lookup_uses_the_normalized_login(resolve_with):
    transport = lookup_transport()

    resolve_with(transport, "https://www.twitch.tv/SHROUD/")

    assert transport.request_for(HELIX_USERS_URL).url.params["login"] == "shroud"


def test_a_bare_login_resolves_the_same_way(resolve_with):
    streamer = resolve_with(lookup_transport(), "shroud")

    assert streamer.username == "shroud"
    assert Streamer.objects.count() == 1


@pytest.mark.parametrize("login", ["a", "ab"])
def test_short_logins_reach_the_lookup_layer(resolve_with, login):
    """A short login is looked up, not refused before Twitch is asked."""
    transport = lookup_transport(users=[twitch_user(user_id="7", login=login, display_name=login)])

    streamer = resolve_with(transport, login)

    assert transport.request_for(HELIX_USERS_URL).url.params["login"] == login
    assert streamer.username == login
    assert streamer.channel_url == f"https://www.twitch.tv/{login}"


@pytest.mark.parametrize("login", ["a", "ab"])
def test_unresolved_short_login_is_reported_as_not_found(resolve_with, login):
    transport = lookup_transport(users=[])

    with pytest.raises(StreamerNotFoundError):
        resolve_with(transport, login)

    assert transport.request_for(HELIX_USERS_URL).url.params["login"] == login
    assert Streamer.objects.count() == 0


# -- not found ---------------------------------------------------------------


def test_unknown_login_raises_not_found(resolve_with):
    with pytest.raises(StreamerNotFoundError):
        resolve_with(lookup_transport(users=[]), "nobodyhere")

    assert Streamer.objects.count() == 0


# -- idempotency -------------------------------------------------------------


def test_resolving_twice_does_not_duplicate(resolve_with):
    first = resolve_with(lookup_transport(), "https://www.twitch.tv/shroud")
    second = resolve_with(lookup_transport(), "twitch.tv/shroud")

    assert Streamer.objects.count() == 1
    assert first.pk == second.pk


def test_resolving_twice_refreshes_mutable_profile_fields(resolve_with):
    resolve_with(lookup_transport(), "shroud")

    updated = resolve_with(
        lookup_transport(
            users=[
                twitch_user(
                    display_name="Shroud Official",
                    profile_image_url="https://static-cdn.example/new.png",
                    broadcaster_type="affiliate",
                    description="Now streaming variety.",
                )
            ]
        ),
        "shroud",
    )

    assert Streamer.objects.count() == 1
    assert updated.display_name == "Shroud Official"
    assert updated.profile_image_url == "https://static-cdn.example/new.png"
    assert updated.broadcaster_type == "affiliate"
    assert updated.description == "Now streaming variety."


def test_the_platform_user_id_is_the_identity_anchor(resolve_with):
    """A renamed channel updates in place rather than creating a second row."""
    original = resolve_with(lookup_transport(), "shroud")

    renamed = resolve_with(
        lookup_transport(
            users=[twitch_user(login="shroudx", display_name="shroudX")],
        ),
        "shroudx",
    )

    assert Streamer.objects.count() == 1
    assert renamed.pk == original.pk
    assert renamed.platform_user_id == "37402112"
    assert renamed.username == "shroudx"
    assert renamed.channel_url == "https://www.twitch.tv/shroudx"


def test_a_different_account_creates_a_separate_streamer(resolve_with):
    resolve_with(lookup_transport(), "shroud")

    other = resolve_with(
        lookup_transport(users=[twitch_user(user_id="99", login="someoneelse")]),
        "someoneelse",
    )

    assert Streamer.objects.count() == 2
    assert other.platform_user_id == "99"


def test_created_at_is_preserved_across_refreshes(resolve_with):
    first = resolve_with(lookup_transport(), "shroud")
    created_at = first.created_at

    second = resolve_with(lookup_transport(), "shroud")

    assert second.created_at == created_at


# -- upsert unit behavior ----------------------------------------------------


def test_upsert_is_idempotent_for_the_same_identity():
    upsert_twitch_streamer(identity())
    upsert_twitch_streamer(identity())

    assert Streamer.objects.count() == 1


def test_upsert_reactivates_a_deactivated_streamer():
    streamer = upsert_twitch_streamer(identity())
    Streamer.objects.filter(pk=streamer.pk).update(is_active=False)

    refreshed = upsert_twitch_streamer(identity())

    assert refreshed.is_active is True


def test_username_taken_by_another_account_raises_a_conflict():
    """Twitch releases abandoned logins; V0 surfaces the clash rather than guessing."""
    upsert_twitch_streamer(identity(user_id="1", login="shroud"))

    with pytest.raises(StreamerConflictError):
        upsert_twitch_streamer(identity(user_id="2", login="shroud"))

    assert Streamer.objects.count() == 1
    assert Streamer.objects.get().platform_user_id == "1"


def test_no_live_state_is_recorded():
    """Live status belongs to stream monitoring, not to streamer identity."""
    streamer = upsert_twitch_streamer(identity())

    field_names = {field.name for field in streamer._meta.get_fields()}
    for forbidden in (
        "is_live",
        "live",
        "viewer_count",
        "follower_count",
        "stream_title",
        "game_name",
        "category",
        "started_at",
    ):
        assert forbidden not in field_names


def test_repr_and_str_are_readable():
    streamer = upsert_twitch_streamer(identity())

    assert "shroud" in str(streamer)
    assert "shroud" in repr(streamer)
