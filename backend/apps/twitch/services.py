"""Service layer for the Twitch integration.

Everything above this layer (views today, domain code in later milestones) works
with the functions here and never touches `TwitchClient` or Twitch URLs
directly.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.cache import cache
from django.db import transaction

from apps.twitch.client import (
    TokenResponse,
    TokenValidation,
    TwitchClient,
    TwitchIdentity,
)
from apps.twitch.exceptions import TwitchAuthenticationError
from apps.twitch.models import TwitchConnection

logger = logging.getLogger(__name__)

APP_TOKEN_CACHE_KEY = "twitch:app_access_token"

# Twitch app tokens are long-lived, but a token is dropped from the cache well
# before Twitch expires it so a request is never started with one about to die.
APP_TOKEN_SAFETY_MARGIN_SECONDS = 300


def get_app_access_token(*, client: TwitchClient | None = None, force_refresh: bool = False) -> str:
    """Return a usable app access token, minting one only when needed.

    The token is cached rather than persisted: it is derived from the client
    credentials and can be re-obtained at any time, so there is nothing worth
    keeping in the database.

    Two concurrent cold requests may each ask Twitch for a token. That is
    harmless — Twitch issues app tokens freely and the last write wins — so no
    distributed lock is introduced for it at this stage.
    """
    if not force_refresh:
        cached = cache.get(APP_TOKEN_CACHE_KEY)
        if isinstance(cached, str) and cached:
            return cached

    client = client or TwitchClient()
    grant = client.fetch_app_access_token()

    ttl = grant.expires_in - APP_TOKEN_SAFETY_MARGIN_SECONDS
    if ttl > 0:
        cache.set(APP_TOKEN_CACHE_KEY, grant.access_token, timeout=ttl)
    else:
        # Shorter-lived than the safety margin: usable once, but not worth caching.
        logger.info("Twitch app token expires within the safety margin; not caching it.")
    return grant.access_token


def clear_app_access_token() -> None:
    cache.delete(APP_TOKEN_CACHE_KEY)


@transaction.atomic
def store_connection(
    *,
    identity: TwitchIdentity,
    grant: TokenResponse,
) -> TwitchConnection:
    """Create or update the local connection for an authorized Twitch account."""
    connection = TwitchConnection.objects.filter(twitch_user_id=identity.user_id).first()
    if connection is None:
        connection = TwitchConnection(twitch_user_id=identity.user_id)
    connection.login = identity.login
    connection.display_name = identity.display_name
    connection.apply_tokens(
        access_token=grant.access_token,
        expires_in=grant.expires_in,
        refresh_token=grant.refresh_token,
        scopes=grant.scopes,
    )
    connection.save()
    logger.info("Stored Twitch connection for user_id=%s.", identity.user_id)
    return connection


def complete_authorization(code: str, *, client: TwitchClient | None = None) -> TwitchConnection:
    """Exchange an authorization code, read the identity and persist both."""
    client = client or TwitchClient()
    grant = client.exchange_authorization_code(code)
    identity = client.get_authenticated_user(grant.access_token)
    return store_connection(identity=identity, grant=grant)


def refresh_connection(
    connection: TwitchConnection,
    *,
    client: TwitchClient | None = None,
) -> TwitchConnection:
    """Refresh a connection's access token and persist the result.

    A failed refresh is terminal: the connection is flagged as needing
    re-authorization and a typed authentication error is raised. There is no
    retry, so this cannot loop.
    """
    client = client or TwitchClient()
    try:
        grant = client.refresh_access_token(connection.get_refresh_token())
    except TwitchAuthenticationError:
        connection.mark_requires_reauthorization()
        logger.warning(
            "Twitch refresh failed for user_id=%s; re-authorization required.",
            connection.twitch_user_id,
        )
        raise

    connection.apply_tokens(
        access_token=grant.access_token,
        expires_in=grant.expires_in,
        refresh_token=grant.refresh_token,
        scopes=grant.scopes or tuple(connection.scopes),
    )
    connection.save()
    return connection


def validate_connection(
    connection: TwitchConnection,
    *,
    client: TwitchClient | None = None,
) -> TokenValidation:
    """Ask Twitch whether the stored access token is still live.

    A revoked or invalid token flags the connection for re-authorization and
    raises; it is never retried in a loop.

    Deferred runtime obligation: Twitch requires third-party applications that
    maintain OAuth sessions to validate the access token when the application or
    session starts, and hourly thereafter. This function is the mechanism for
    that, but nothing calls it on a schedule yet — there is no recurring
    validation job in V0. Wiring it to a periodic task belongs to a later
    runtime-monitoring milestone.
    """
    client = client or TwitchClient()
    try:
        return client.validate_token(connection.get_access_token())
    except TwitchAuthenticationError:
        connection.mark_requires_reauthorization()
        raise


def helix_get_as_connection(
    connection: TwitchConnection,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    client: TwitchClient | None = None,
) -> dict[str, Any]:
    """Authenticated Helix GET on behalf of the connection.

    The request is made with the stored access token as-is. Twitch's guidance is
    to react to a 401 rather than to refresh pre-emptively from a locally
    tracked expiry, so `token_expires_at` is never consulted here: a token that
    looks expired locally may still be live, and one that looks live may already
    have been revoked. Only Twitch's answer decides.

    On a 401 the connection is refreshed exactly once and the request replayed
    exactly once. A second 401 marks the connection as needing re-authorization
    and propagates. A failed refresh does the same. Either way the cycle is
    bounded and cannot loop.
    """
    client = client or TwitchClient()
    try:
        return client.helix_get(path, access_token=connection.get_access_token(), params=params)
    except TwitchAuthenticationError:
        logger.info("Twitch rejected the access token; refreshing once and retrying.")

    # Raises a typed authentication error, having flagged the connection, if the
    # refresh itself fails. There is no second refresh attempt beyond this point.
    connection = refresh_connection(connection, client=client)

    try:
        return client.helix_get(path, access_token=connection.get_access_token(), params=params)
    except TwitchAuthenticationError:
        logger.warning(
            "Twitch rejected the refreshed access token for user_id=%s; re-authorization required.",
            connection.twitch_user_id,
        )
        connection.mark_requires_reauthorization()
        raise


def build_connection_status(connection: TwitchConnection | None) -> dict[str, Any]:
    """Safe, public-facing view of the connection. Carries no token material."""
    if connection is None:
        return {
            "connected": False,
            "account": None,
            "scopes": [],
            "requires_reauthorization": False,
        }
    return {
        "connected": True,
        "account": {
            "id": connection.twitch_user_id,
            "login": connection.login,
            "display_name": connection.display_name,
        },
        "scopes": list(connection.scopes),
        "requires_reauthorization": connection.requires_reauthorization,
    }
