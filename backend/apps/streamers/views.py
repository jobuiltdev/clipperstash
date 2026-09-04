"""HTTP entry point for streamer resolution.

Every failure is mapped to a short, stable code and a message safe to show a
user. Twitch response bodies, tokens, the client secret and HTTP-library
exceptions never reach a response.
"""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from apps.streamers import services
from apps.streamers.exceptions import (
    StreamerConflictError,
    StreamerError,
    StreamerInputError,
    StreamerNotFoundError,
)
from apps.streamers.serializers import ResolveStreamerRequestSerializer, StreamerSerializer
from apps.twitch.exceptions import (
    TwitchAPIError,
    TwitchAuthenticationError,
    TwitchConfigurationError,
)

logger = logging.getLogger(__name__)

ERROR_STATUS: dict[type[StreamerError], int] = {
    StreamerInputError: status.HTTP_400_BAD_REQUEST,
    StreamerNotFoundError: status.HTTP_404_NOT_FOUND,
    StreamerConflictError: status.HTTP_409_CONFLICT,
}


def _error(code: str, message: str, http_status: int) -> Response:
    return Response({"error": {"code": code, "message": message}}, status=http_status)


@api_view(["POST"])
def resolve_streamer(request: Request) -> Response:
    """Resolve a Twitch channel URL or login to a persisted streamer."""
    body = ResolveStreamerRequestSerializer(data=request.data)
    if not body.is_valid():
        return _error(
            StreamerInputError.code,
            StreamerInputError.message,
            status.HTTP_400_BAD_REQUEST,
        )

    try:
        streamer = services.resolve_streamer(body.validated_data["input"])
    except StreamerError as exc:
        return _error(
            exc.code,
            exc.message,
            ERROR_STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST),
        )
    except TwitchConfigurationError:
        logger.warning("Streamer resolution attempted while Twitch is unconfigured.")
        return _error(
            "twitch_not_configured",
            "Twitch is not configured for this installation.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except (TwitchAuthenticationError, TwitchAPIError):
        # The typed exception carries only a status code and Twitch's own error
        # text; neither is echoed to the caller.
        logger.warning("Streamer resolution failed: Twitch did not complete the request.")
        return _error(
            "twitch_unavailable",
            "Twitch could not be reached right now. Try again shortly.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return Response({"streamer": StreamerSerializer(streamer).data})
