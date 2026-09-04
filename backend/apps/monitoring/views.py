"""HTTP entry point for observing a streamer's live state.

Failures are reported as an explicit unavailable state, never as "offline", and
carry no Twitch response body, token or internal detail.
"""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from apps.monitoring import services
from apps.monitoring.exceptions import StreamStateUnavailableError
from apps.monitoring.serializers import ObservedStreamerSerializer, ObservedStreamSerializer
from apps.streamers.models import Streamer
from apps.twitch.exceptions import TwitchConfigurationError

logger = logging.getLogger(__name__)


def _error(code: str, message: str, http_status: int) -> Response:
    return Response({"error": {"code": code, "message": message}}, status=http_status)


@api_view(["POST"])
def observe_streamer(_request: Request, streamer_id: int) -> Response:
    """Check whether a resolved streamer is live right now."""
    streamer = Streamer.objects.filter(pk=streamer_id).first()
    if streamer is None:
        return _error(
            "streamer_not_found",
            "That streamer has not been resolved.",
            status.HTTP_404_NOT_FOUND,
        )

    try:
        observation = services.observe_streamer(streamer)
    except TwitchConfigurationError:
        logger.warning("Observation attempted while Twitch is unconfigured.")
        return _error(
            "twitch_not_configured",
            "Twitch is not configured for this installation.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except StreamStateUnavailableError as exc:
        # Deliberately not reported as "offline": nothing was determined.
        return _error(exc.code, exc.message, status.HTTP_503_SERVICE_UNAVAILABLE)

    return Response(
        {
            "status": observation.status,
            "streamer": ObservedStreamerSerializer(observation.streamer).data,
            "stream": (
                ObservedStreamSerializer(observation.session).data
                if observation.session is not None
                else None
            ),
        }
    )
