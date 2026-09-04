"""Stream observation and session lifecycle.

One entry point, `observe_streamer()`, asks Twitch whether a resolved streamer is
live and records the answer. All lifecycle rules live here so that views, and
later a scheduler, never re-implement them.

The central rule is that only a successful Twitch response with no stream counts
as an offline observation. Any failure leaves every session exactly as it was.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from apps.monitoring.exceptions import StreamStateUnavailableError
from apps.monitoring.models import StreamSession, StreamSessionStatus
from apps.streamers.models import Streamer
from apps.twitch import services as twitch_services
from apps.twitch.client import TwitchClient, TwitchStream
from apps.twitch.exceptions import TwitchAPIError, TwitchAuthenticationError

logger = logging.getLogger(__name__)

LIVE = "live"
OFFLINE = "offline"


@dataclass(frozen=True)
class Observation:
    """The outcome of one observation.

    There is no "unknown" member: a failed observation raises
    `StreamStateUnavailableError` rather than being representable as a result,
    so it cannot be confused with `OFFLINE` further up the stack.
    """

    status: str
    streamer: Streamer
    session: StreamSession | None
    observed_at: datetime

    @property
    def is_live(self) -> bool:
        return self.status == LIVE


def observe_streamer(streamer: Streamer, *, client: TwitchClient | None = None) -> Observation:
    """Ask Twitch whether `streamer` is live, and record the answer.

    Uses the streamer's stored platform account id, so the channel is never
    re-resolved through Get Users just to check live state, and no connected
    operator OAuth token is involved.
    """
    try:
        stream = twitch_services.lookup_stream_by_user_id(streamer.platform_user_id, client=client)
    except (TwitchAuthenticationError, TwitchAPIError) as exc:
        # Nothing has been written at this point, and nothing will be: an
        # unreadable answer must never close or open a session.
        logger.warning(
            "Stream state for streamer_id=%s is unavailable; sessions left untouched.",
            streamer.pk,
        )
        raise StreamStateUnavailableError() from exc

    observed_at = timezone.now()

    if stream is None:
        _close_live_sessions(streamer, observed_at=observed_at)
        return Observation(status=OFFLINE, streamer=streamer, session=None, observed_at=observed_at)

    session = _record_live_stream(streamer, stream, observed_at=observed_at)
    return Observation(status=LIVE, streamer=streamer, session=session, observed_at=observed_at)


@transaction.atomic
def _close_live_sessions(streamer: Streamer, *, observed_at: datetime) -> int:
    """Close whatever live session the streamer has. Idempotent.

    A second offline observation matches nothing and therefore changes nothing,
    leaving the already-recorded `ended_at` intact.
    """
    closed = StreamSession.objects.filter(
        streamer=streamer, status=StreamSessionStatus.LIVE
    ).update(
        status=StreamSessionStatus.ENDED,
        ended_at=observed_at,
        updated_at=observed_at,
    )
    if closed:
        logger.info("Closed %s live session(s) for streamer_id=%s.", closed, streamer.pk)
    return closed


@transaction.atomic
def _record_live_stream(
    streamer: Streamer,
    stream: TwitchStream,
    *,
    observed_at: datetime,
) -> StreamSession:
    """Create or refresh the session for an observed broadcast.

    The streamer row is locked for the duration, so two near-simultaneous
    observations of the same channel are serialized by the database rather than
    racing. The conditional unique constraint is the backstop.
    """
    Streamer.objects.select_for_update().filter(pk=streamer.pk).first()

    # A different broadcast is already recorded as live: the previous one ended
    # at some point ClipperStash did not observe, so close it now.
    superseded = (
        StreamSession.objects.filter(streamer=streamer, status=StreamSessionStatus.LIVE)
        .exclude(platform_stream_id=stream.stream_id)
        .update(
            status=StreamSessionStatus.ENDED,
            ended_at=observed_at,
            updated_at=observed_at,
        )
    )
    if superseded:
        logger.info(
            "Superseded %s live session(s) for streamer_id=%s by a new broadcast.",
            superseded,
            streamer.pk,
        )

    session, created = StreamSession.objects.get_or_create(
        platform_stream_id=stream.stream_id,
        defaults={
            "streamer": streamer,
            # Twitch's own start time, never the local clock.
            "started_at": stream.started_at,
            "title": stream.title,
            "category_id": stream.game_id,
            "category_name": stream.game_name,
            "language": stream.language,
            "is_mature": stream.is_mature,
            "last_viewer_count": stream.viewer_count,
            "last_observed_at": observed_at,
            "status": StreamSessionStatus.LIVE,
        },
    )

    if created:
        logger.info(
            "Opened stream session for streamer_id=%s stream=%s.",
            streamer.pk,
            stream.stream_id,
        )
        return session

    # Same broadcast seen again: refresh what can change, leave started_at alone.
    session.title = stream.title
    session.category_id = stream.game_id
    session.category_name = stream.game_name
    session.language = stream.language
    session.is_mature = stream.is_mature
    session.last_viewer_count = stream.viewer_count
    session.last_observed_at = observed_at
    session.status = StreamSessionStatus.LIVE
    session.ended_at = None
    session.save()
    return session


def current_session(streamer: Streamer) -> StreamSession | None:
    """The streamer's live session, if any."""
    return StreamSession.objects.filter(streamer=streamer, status=StreamSessionStatus.LIVE).first()
