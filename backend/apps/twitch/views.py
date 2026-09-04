"""HTTP entry points for the Twitch OAuth flow and connection status.

None of these responses carry token material, the client secret or an
authorization code, and none of them log those values either.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponseBase
from django.shortcuts import redirect
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from apps.twitch import oauth, services
from apps.twitch.client import TwitchCredentials
from apps.twitch.exceptions import (
    TwitchAPIError,
    TwitchAuthenticationError,
    TwitchConfigurationError,
    TwitchOAuthStateError,
)
from apps.twitch.models import TwitchConnection

logger = logging.getLogger(__name__)


def _frontend_redirect(outcome: str, *, reason: str | None = None) -> HttpResponseBase:
    """Send the browser back to the frontend with a short, safe outcome flag."""
    params = {"twitch": outcome}
    if reason:
        params["reason"] = reason
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return redirect(f"{base}/?{urlencode(params)}")


@api_view(["GET"])
def oauth_start(_request: Request) -> HttpResponseBase:
    """Begin the Authorization Code flow by redirecting to Twitch."""
    try:
        credentials = TwitchCredentials.from_settings()
    except TwitchConfigurationError as exc:
        return Response(
            {"detail": str(exc)},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    state = oauth.create_state()
    authorization_url = oauth.build_authorization_url(credentials, state=state)
    logger.info("Starting Twitch authorization with scopes=%s.", list(oauth.REQUIRED_SCOPES))
    return redirect(authorization_url)


@api_view(["GET"])
def oauth_callback(request: Request) -> HttpResponseBase:
    """Handle Twitch's redirect back, exchanging the code server-side."""
    error = request.query_params.get("error")
    state = request.query_params.get("state")
    code = request.query_params.get("code")

    if error:
        # `access_denied` is the operator declining; treat any error as a clean stop.
        logger.info("Twitch authorization was not granted (error=%s).", error)
        oauth.consume_state_quietly(state)
        return _frontend_redirect("denied", reason=error)

    try:
        oauth.consume_state(state)
    except TwitchOAuthStateError as exc:
        logger.warning("Rejected Twitch callback: %s", exc)
        return _frontend_redirect("error", reason="invalid_state")

    if not code:
        logger.warning("Rejected Twitch callback: no authorization code was supplied.")
        return _frontend_redirect("error", reason="missing_code")

    try:
        services.complete_authorization(code)
    except TwitchConfigurationError:
        return _frontend_redirect("error", reason="not_configured")
    except TwitchAuthenticationError:
        logger.warning("Twitch rejected the authorization code exchange.")
        return _frontend_redirect("error", reason="exchange_failed")
    except TwitchAPIError:
        logger.warning("Twitch authorization code exchange did not complete.")
        return _frontend_redirect("error", reason="exchange_failed")

    return _frontend_redirect("connected")


@api_view(["GET"])
def connection_status(_request: Request) -> Response:
    """Report whether a Twitch account is connected, and which scopes it granted."""
    connection = TwitchConnection.objects.current()
    return Response(services.build_connection_status(connection))
