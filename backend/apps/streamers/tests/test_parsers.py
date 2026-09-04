"""Tests for the pure input parser. No I/O, no database, no network."""

from __future__ import annotations

import pytest

from apps.streamers.exceptions import StreamerInputError
from apps.streamers.parsers import canonical_channel_url, parse_twitch_login


@pytest.mark.parametrize(
    ("submitted", "expected"),
    [
        ("shroud", "shroud"),
        ("SHROUD", "shroud"),
        ("Shroud", "shroud"),
        ("some_user", "some_user"),
        ("user123", "user123"),
        ("a", "a"),
        ("ab", "ab"),
        ("A", "a"),
        ("AB", "ab"),
        ("_", "_"),
        ("1", "1"),
        ("a" * 25, "a" * 25),
        ("https://www.twitch.tv/a", "a"),
        ("https://www.twitch.tv/AB/", "ab"),
        ("twitch.tv/a", "a"),
        ("https://www.twitch.tv/shroud", "shroud"),
        ("https://twitch.tv/shroud", "shroud"),
        ("http://www.twitch.tv/shroud", "shroud"),
        ("http://twitch.tv/shroud", "shroud"),
        ("www.twitch.tv/shroud", "shroud"),
        ("twitch.tv/shroud", "shroud"),
        ("https://www.twitch.tv/shroud/", "shroud"),
        ("twitch.tv/shroud/", "shroud"),
        ("https://www.twitch.tv/Shroud", "shroud"),
        ("HTTPS://WWW.TWITCH.TV/Shroud", "shroud"),
        ("  shroud  ", "shroud"),
        ("\t https://www.twitch.tv/shroud/ \n", "shroud"),
        ("https://WWW.Twitch.TV/shroud", "shroud"),
    ],
)
def test_accepted_inputs_normalize_to_a_login(submitted, expected):
    assert parse_twitch_login(submitted) == expected


@pytest.mark.parametrize(
    "submitted",
    [
        "",
        "   ",
        "\t\n",
        None,
        123,
    ],
)
def test_empty_or_non_string_input_is_rejected(submitted):
    with pytest.raises(StreamerInputError):
        parse_twitch_login(submitted)


@pytest.mark.parametrize(
    "submitted",
    [
        "https://www.twitch.tv/videos/123456",
        "https://twitch.tv/shroud/videos",
        "https://twitch.tv/shroud/clips",
        "https://www.twitch.tv/directory/game/Chess",
        "https://twitch.tv/a/b/c",
    ],
)
def test_nested_routes_are_rejected(submitted):
    with pytest.raises(StreamerInputError):
        parse_twitch_login(submitted)


@pytest.mark.parametrize(
    "route",
    [
        "directory",
        "downloads",
        "jobs",
        "settings",
        "subscriptions",
        "inventory",
        "wallet",
        "videos",
    ],
)
def test_reserved_twitch_routes_are_rejected(route):
    with pytest.raises(StreamerInputError):
        parse_twitch_login(f"https://www.twitch.tv/{route}")

    with pytest.raises(StreamerInputError):
        parse_twitch_login(route)


@pytest.mark.parametrize(
    "submitted",
    [
        "https://twitch.tv",
        "https://twitch.tv/",
        "https://www.twitch.tv/",
        "twitch.tv/",
        "twitch.tv",
    ],
)
def test_twitch_url_without_a_channel_is_rejected(submitted):
    with pytest.raises(StreamerInputError):
        parse_twitch_login(submitted)


@pytest.mark.parametrize(
    "submitted",
    [
        "https://youtube.com/shroud",
        "https://kick.com/shroud",
        "evil.example/shroud",
        "https://evil.example/twitch.tv/shroud",
        "evil.example/twitch.tv/name",
    ],
)
def test_unsupported_hosts_are_rejected(submitted):
    with pytest.raises(StreamerInputError):
        parse_twitch_login(submitted)


@pytest.mark.parametrize(
    "submitted",
    [
        # Suffix and prefix look-alikes.
        "twitch.tv.evil.example/name",
        "https://twitch.tv.evil.example/name",
        "https://www.twitch.tv.evil.example/name",
        "nottwitch.tv/name",
        "https://twitchtv/name",
        # Userinfo that makes another host read as Twitch.
        "twitch.tv@evil.example/name",
        "https://twitch.tv@evil.example/name",
        "https://www.twitch.tv@evil.example/name",
        "https://user:pass@evil.example/name",
    ],
)
def test_host_confusion_attempts_are_rejected(submitted):
    """None of these address twitch.tv, however much they look like it."""
    with pytest.raises(StreamerInputError):
        parse_twitch_login(submitted)


@pytest.mark.parametrize(
    "submitted",
    [
        "ftp://twitch.tv/shroud",
        "file://twitch.tv/shroud",
        "javascript:alert(1)",
        "data:text/html,hi",
        "ht!tp://twitch.tv/shroud",
        "https://",
        "http://",
        "://twitch.tv/shroud",
        "https://twitch.tv:8080/shroud",
        "https://twitch.tv:notaport/shroud",
    ],
)
def test_malformed_or_unsupported_urls_are_rejected(submitted):
    with pytest.raises(StreamerInputError):
        parse_twitch_login(submitted)


@pytest.mark.parametrize(
    "submitted",
    [
        "https://twitch.tv/?channel=evil",
        "https://twitch.tv/shroud?x=1",
        "https://twitch.tv/shroud#/videos/1",
        "https://twitch.tv/#/shroud",
        "twitch.tv/shroud?redirect=https://evil.example",
    ],
)
def test_query_and_fragment_bearing_urls_are_rejected(submitted):
    """Ignoring them silently could change which channel was meant."""
    with pytest.raises(StreamerInputError):
        parse_twitch_login(submitted)


@pytest.mark.parametrize(
    "submitted",
    [
        "a" * 26,
        "a" * 100,
        "sh roud",
        "shroud!",
        "shr-oud",
        "shr.oud",
        "shroud$",
        "user@name",
        "ünicode",
        "../shroud",
    ],
)
def test_impossible_logins_are_rejected(submitted):
    with pytest.raises(StreamerInputError):
        parse_twitch_login(submitted)


def test_absurdly_long_input_is_rejected():
    with pytest.raises(StreamerInputError):
        parse_twitch_login("https://twitch.tv/" + "a" * 5000)


def test_rejection_messages_are_user_safe():
    """Messages help the user; they never echo the submitted value back."""
    hostile = "https://twitch.tv@evil.example/name"

    with pytest.raises(StreamerInputError) as exc_info:
        parse_twitch_login(hostile)

    message = str(exc_info.value)
    assert "evil.example" not in message
    assert message


def test_canonical_channel_url_uses_the_www_form():
    assert canonical_channel_url("shroud") == "https://www.twitch.tv/shroud"


@pytest.mark.parametrize("login", ["a", "ab", "a1", "_x", "abc"])
def test_short_logins_are_not_rejected_locally(login):
    """No minimum length is imposed; Helix decides whether the account exists.

    Twitch's current registration rules are not a guarantee about accounts that
    already exist, so a syntactically valid short login is passed through to be
    looked up rather than refused here.
    """
    assert parse_twitch_login(login) == login
    assert parse_twitch_login(f"https://www.twitch.tv/{login}") == login


@pytest.mark.parametrize("length", [1, 2, 3, 24, 25])
def test_logins_up_to_the_length_limit_are_accepted(length):
    login = "a" * length

    assert parse_twitch_login(login) == login


@pytest.mark.parametrize("length", [26, 40, 200])
def test_logins_over_the_length_limit_are_rejected(length):
    with pytest.raises(StreamerInputError):
        parse_twitch_login("a" * length)


@pytest.mark.parametrize("login", ["a-", "a.", "a!", "a b", "a@", "é", "a/"])
def test_short_logins_with_invalid_characters_are_still_rejected(login):
    with pytest.raises(StreamerInputError):
        parse_twitch_login(login)
