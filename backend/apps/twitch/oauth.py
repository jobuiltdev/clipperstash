"""Authorization Code flow helpers: scopes, state and the authorization URL.

ClipperStash is a server-backed application, so it uses the Authorization Code
grant. The implicit flow is deliberately not supported: it would hand a token to
the browser, and the client secret would be unusable for refresh.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

from django.core.cache import cache

from apps.twitch.client import OAUTH_AUTHORIZE_URL, TwitchCredentials
from apps.twitch.exceptions import TwitchOAuthStateError

# The scopes V0 needs, and nothing more.
#
#   clips:edit      clip creation in a later milestone
#   user:read:chat  reading chat over an EventSub WebSocket, which Twitch only
#                   permits with a user access token carrying this scope
#
# The account's email address is never requested, and no bot, moderator or
# send-message scope is requested: none is required to *receive*
# channel.chat.message over a WebSocket authorized by the reading user.
REQUIRED_SCOPES: tuple[str, ...] = ("clips:edit", "user:read:chat")

# The scope that gates chat monitoring specifically.
CHAT_READ_SCOPE = "user:read:chat"

STATE_CACHE_PREFIX = "twitch:oauth:state:"
STATE_TTL_SECONDS = 600
STATE_BYTES = 32


def _state_key(state: str) -> str:
    return f"{STATE_CACHE_PREFIX}{state}"


def create_state() -> str:
    """Mint a cryptographically secure state value and record it server-side.

    The value is held in the shared cache rather than in a client-readable
    cookie, so the browser never carries anything that could be replayed after
    it has been consumed.
    """
    state = secrets.token_urlsafe(STATE_BYTES)
    cache.set(_state_key(state), True, timeout=STATE_TTL_SECONDS)
    return state


def consume_state(state: str | None) -> None:
    """Validate a returned state and retire it.

    Raises `TwitchOAuthStateError` when the state is missing, unknown, expired
    or already used. Consumption is atomic against the cache, so a state that
    arrives twice is only ever accepted once.
    """
    if not state:
        raise TwitchOAuthStateError("The OAuth state parameter is missing.")

    # `delete` reports whether the key existed, which makes accept-and-retire a
    # single cache round trip and leaves no window for a concurrent replay.
    if not cache.delete(_state_key(state)):
        raise TwitchOAuthStateError("The OAuth state is unknown, expired or already used.")


def build_authorization_url(
    credentials: TwitchCredentials,
    *,
    state: str,
    scopes: tuple[str, ...] = REQUIRED_SCOPES,
) -> str:
    """Build the Twitch authorization URL. Carries no secret."""
    query = urlencode(
        {
            "client_id": credentials.client_id,
            "redirect_uri": credentials.redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
        }
    )
    return f"{OAUTH_AUTHORIZE_URL}?{query}"


def consume_state_quietly(state: str | None) -> None:
    """Retire a state without raising, for paths that already failed.

    Used when Twitch reports the operator declined: the state is spent either
    way and should not stay usable.
    """
    if state:
        cache.delete(_state_key(state))
