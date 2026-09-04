"""Pure parsing and normalization of submitted streamer input.

This module performs no I/O. It turns whatever the user typed into a normalized
Twitch login, or refuses. Twitch's Helix lookup remains the authority on whether
that login actually exists — local syntax checks only reject values that could
not possibly be a channel.

The submitted URL is never fetched. It is parsed for its host and path and then
discarded, so a hostile value cannot steer an outbound request.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from apps.streamers.exceptions import StreamerInputError

TWITCH_HOSTS = frozenset({"twitch.tv", "www.twitch.tv"})
ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_PORTS = frozenset({None, 80, 443})

CANONICAL_CHANNEL_HOST = "https://www.twitch.tv"

# Twitch logins are ASCII letters, digits and underscores, up to 25 characters.
# No minimum length is imposed: Twitch's current registration rules are not a
# guarantee about every account that already exists, and excluding a short login
# locally would hide a real channel. Helix decides what actually exists.
LOGIN_PATTERN = re.compile(r"^[a-z0-9_]{1,25}$")

# Matches a leading URL scheme, so a bare "twitch.tv/name" can be told apart
# from an explicit "https://twitch.tv/name".
SCHEME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")

MAX_INPUT_LENGTH = 2048

# Twitch root paths that are site routes rather than channels. This is a guard
# against obvious mistakes, not a mirror of Twitch's routing table; Helix is
# still the final authority on whether a login resolves.
RESERVED_ROUTES = frozenset(
    {
        "about",
        "broadcast",
        "dashboard",
        "directory",
        "downloads",
        "drops",
        "following",
        "friends",
        "inventory",
        "jobs",
        "login",
        "moderator",
        "payments",
        "popout",
        "prime",
        "search",
        "settings",
        "signup",
        "store",
        "subscriptions",
        "turbo",
        "videos",
        "wallet",
    }
)


def canonical_channel_url(login: str) -> str:
    """The canonical ClipperStash form of a Twitch channel URL."""
    return f"{CANONICAL_CHANNEL_HOST}/{login}"


def _validated_login(candidate: str) -> str:
    login = candidate.strip().lower()
    if not login:
        raise StreamerInputError("Enter a Twitch channel URL or username.")
    if login in RESERVED_ROUTES:
        raise StreamerInputError("That is a Twitch site page, not a channel.")
    if not LOGIN_PATTERN.fullmatch(login):
        raise StreamerInputError()
    return login


def _login_from_url(value: str) -> str:
    # A value with no scheme is still a URL if it names a host, so give bare
    # forms like "twitch.tv/name" a scheme before parsing. urlsplit would
    # otherwise read the whole string as a path.
    candidate = value if SCHEME_PATTERN.match(value) else f"https://{value}"

    try:
        parts = urlsplit(candidate)
    except ValueError:
        raise StreamerInputError() from None

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise StreamerInputError()

    # Userinfo like "twitch.tv@evil.example" makes a URL read as Twitch while
    # actually addressing another host. Refuse rather than resolve it.
    if parts.username or parts.password or "@" in parts.netloc:
        raise StreamerInputError()

    try:
        port = parts.port
    except ValueError:
        raise StreamerInputError() from None
    if port not in DEFAULT_PORTS:
        raise StreamerInputError()

    host = (parts.hostname or "").lower().rstrip(".")
    if host not in TWITCH_HOSTS:
        raise StreamerInputError("Enter a twitch.tv channel URL or username.")

    # A query string or fragment carries routing this parser does not interpret.
    # Dropping it silently could change which channel was meant, so refuse.
    if parts.query or parts.fragment:
        raise StreamerInputError("Enter a plain Twitch channel URL, without extra parameters.")

    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments:
        raise StreamerInputError("That URL does not name a Twitch channel.")
    if len(segments) > 1:
        raise StreamerInputError("That is a Twitch page inside a channel, not the channel itself.")

    return _validated_login(segments[0])


def parse_twitch_login(value: str | None) -> str:
    """Normalize submitted input to a lowercase Twitch login.

    Accepts a bare login or a twitch.tv / www.twitch.tv channel URL over http or
    https, with or without a trailing slash and surrounding whitespace. Anything
    else raises `StreamerInputError`.
    """
    if not isinstance(value, str):
        raise StreamerInputError("Enter a Twitch channel URL or username.")

    raw = value.strip()
    if not raw:
        raise StreamerInputError("Enter a Twitch channel URL or username.")
    if len(raw) > MAX_INPUT_LENGTH:
        raise StreamerInputError()

    # A bare login cannot contain a separator, so anything with one is a URL.
    if not any(character in raw for character in "/:.@?#\\ "):
        return _validated_login(raw)

    return _login_from_url(raw)
