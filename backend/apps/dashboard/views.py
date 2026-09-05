"""Read-only HTTP surface for the operator dashboard.

Every view here is a `GET`. Nothing in this module writes to the database,
calls Twitch, runs the detector, requests a clip, or advances any lifecycle
state — a dashboard that could change what it is describing would not be an
observation tool. DRF answers any other method with 405, so that guarantee is
enforced by the routing rather than by convention.

Errors use the same `{"error": {"code", "message"}}` envelope as the rest of
the API, and carry nothing but a short controlled string.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from apps.dashboard import queries
from apps.dashboard.serializers import (
    DetectorConfigSerializer,
    MomentDetailSerializer,
    MomentFeedSerializer,
    MomentSummarySerializer,
    StreamSessionDetailSerializer,
    StreamSessionSummarySerializer,
)
from apps.monitoring.models import StreamSession, StreamSessionStatus
from apps.streamers.models import Streamer

# Every list is bounded. An operator asking for a page cannot ask the database
# for an entire season of moments, whatever they put in the query string.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
# The overview is a summary, not a browser; each of its lists is a short teaser
# linking to the page that does paginate.
OVERVIEW_LIMIT = 5


class QueryParameterError(Exception):
    """A query parameter was not something this endpoint can act on."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _error(code: str, message: str, http_status: int) -> Response:
    return Response({"error": {"code": code, "message": message}}, status=http_status)


def _bounded_int(request: Request, name: str, default: int, minimum: int, maximum: int) -> int:
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise QueryParameterError(f"`{name}` must be a whole number.") from exc
    if value < minimum:
        raise QueryParameterError(f"`{name}` must be at least {minimum}.")
    return min(value, maximum)


def _page(queryset, serializer_class, request: Request) -> dict:
    """One bounded window over a queryset, with the total it was drawn from.

    Two statements regardless of page size: one count, one slice. The count is
    what lets the interface say "12 of 340" without fetching 340 rows.
    """
    limit = _bounded_int(request, "limit", DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT)
    offset = _bounded_int(request, "offset", 0, minimum=0, maximum=1_000_000)

    total = queryset.count()
    rows = list(queryset[offset : offset + limit])
    return {
        "count": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
        "results": serializer_class(rows, many=True).data,
    }


@api_view(["GET"])
def overview(request: Request) -> Response:
    """Whole-installation state: the tallies, plus a short recent feed.

    The moment tallies partition every candidate exactly once, so the summary
    cards sum to the total, and a moment whose clip outcome is unknown is
    reported as its own number rather than folded into failures.
    """
    try:
        limit = _bounded_int(request, "limit", OVERVIEW_LIMIT, minimum=1, maximum=MAX_LIMIT)
    except QueryParameterError as exc:
        return _error("invalid_query_parameter", exc.message, status.HTTP_400_BAD_REQUEST)

    sessions = queries.session_queryset()
    moments = queries.moment_queryset()

    live_sessions = sessions.filter(status=StreamSessionStatus.LIVE)[:limit]
    recent_sessions = sessions[:limit]
    recent_moments = moments.order_by("-detected_at", "-id")[:limit]
    # Verified clips only: ones Twitch is confirmed to have created. A request
    # that was merely accepted, one whose outcome was never learned, and one
    # that failed are all excluded — they remain visible through the recent
    # moments feed, the summary counts and session inspection.
    recent_clips = queries.verified_clip_moments(moments)[:limit]

    return Response(
        {
            "generated_at": timezone.now(),
            "counts": queries.overview_counts(),
            "live_sessions": StreamSessionSummarySerializer(live_sessions, many=True).data,
            "recent_sessions": StreamSessionSummarySerializer(recent_sessions, many=True).data,
            "recent_moments": MomentFeedSerializer(recent_moments, many=True).data,
            "recent_clips": MomentFeedSerializer(recent_clips, many=True).data,
        }
    )


@api_view(["GET"])
def detector_config(_request: Request) -> Response:
    """The calibration in force, so a score can be read against its threshold.

    Served from the detector's own configuration object. The dashboard restates
    no constant of its own, so it cannot drift out of step with the pipeline.
    """
    payload = DetectorConfigSerializer(DetectorConfigSerializer.current()).data
    return Response({"detector": payload})


@api_view(["GET"])
def streamer_sessions(request: Request, streamer_id: int) -> Response:
    """Every session observed for one streamer, most recent first."""
    streamer = Streamer.objects.filter(pk=streamer_id).first()
    if streamer is None:
        return _error(
            "streamer_not_found",
            "That streamer has not been resolved.",
            status.HTTP_404_NOT_FOUND,
        )

    queryset = queries.session_queryset().filter(streamer=streamer)
    requested_status = request.query_params.get("status")
    if requested_status:
        if requested_status not in StreamSessionStatus.values:
            return _error(
                "invalid_query_parameter",
                "`status` must be one of: " + ", ".join(StreamSessionStatus.values) + ".",
                status.HTTP_400_BAD_REQUEST,
            )
        queryset = queryset.filter(status=requested_status)

    try:
        page = _page(queryset, StreamSessionSummarySerializer, request)
    except QueryParameterError as exc:
        return _error("invalid_query_parameter", exc.message, status.HTTP_400_BAD_REQUEST)

    return Response({"sessions": page})


@api_view(["GET"])
def session_detail(_request: Request, session_id: int) -> Response:
    """One session, with its moment breakdown and how much chat it saw.

    Chat appears only as a total. The transcript exists in the database as
    pipeline input; it is not the dashboard's to publish.
    """
    session = queries.session_queryset().filter(pk=session_id).first()
    if session is None:
        return _error(
            "session_not_found",
            "That stream session does not exist.",
            status.HTTP_404_NOT_FOUND,
        )

    serializer = StreamSessionDetailSerializer(
        session,
        context={"counts": queries.session_counts(session)},
    )
    return Response({"session": serializer.data})


@api_view(["GET"])
def session_moments(request: Request, session_id: int) -> Response:
    """The moments detected in one session, newest first.

    `clip_state` filters on the same predicates the counts are built from, so a
    filtered list always agrees with the number that led the operator to it.
    """
    if not StreamSession.objects.filter(pk=session_id).exists():
        return _error(
            "session_not_found",
            "That stream session does not exist.",
            status.HTTP_404_NOT_FOUND,
        )

    queryset = queries.moment_queryset().filter(session_id=session_id)
    queryset = queryset.order_by("-detected_at", "-id")

    clip_state = request.query_params.get("clip_state")
    if clip_state:
        predicate = queries.CLIP_STATE_FILTERS.get(clip_state)
        if predicate is None:
            return _error(
                "invalid_query_parameter",
                "`clip_state` must be one of: " + ", ".join(queries.CLIP_STATE_FILTERS) + ".",
                status.HTTP_400_BAD_REQUEST,
            )
        queryset = queryset.filter(predicate)

    try:
        page = _page(queryset, MomentSummarySerializer, request)
    except QueryParameterError as exc:
        return _error("invalid_query_parameter", exc.message, status.HTTP_400_BAD_REQUEST)

    return Response({"moments": page})


@api_view(["GET"])
def moment_detail(_request: Request, moment_id: int) -> Response:
    """One moment with the full working behind its score."""
    moment = queries.moment_queryset().filter(pk=moment_id).first()
    if moment is None:
        return _error(
            "moment_not_found",
            "That moment does not exist.",
            status.HTTP_404_NOT_FOUND,
        )

    return Response({"moment": MomentDetailSerializer(moment).data})
