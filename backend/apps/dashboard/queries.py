"""Read queries behind the dashboard.

Kept apart from the views so the shape of each query — and its cost — is
visible in one place. Two rules hold throughout:

* **Nothing here writes.** Every function returns a queryset or a dict of
  numbers. There is no `save`, no `update`, no `create`, no `delete`.
* **Cost does not grow with the page.** Counts are computed as conditional
  aggregates in a single statement, and every list selects the related rows its
  serializer will touch, so rendering fifty moments costs the same number of
  queries as rendering one.
"""

from __future__ import annotations

from django.db.models import Count, Q, QuerySet

from apps.dashboard import state
from apps.moments.models import MomentCandidate, MomentCandidateStatus
from apps.monitoring.models import ChatMessage, StreamSession, StreamSessionStatus
from apps.streamers.models import Streamer

# The display states from `state.py`, expressed as database predicates. They
# partition every candidate exactly once, so summary cards built from them add
# up to the total and never double-count a moment.
_NOT_REQUESTED = Q(status__in=(MomentCandidateStatus.DETECTED, MomentCandidateStatus.REJECTED))
# A claimed request that never recorded a Twitch clip id: the outcome was never
# learned. Deliberately counted apart from both "requested" and "failed".
_REQUEST_UNKNOWN = Q(
    status=MomentCandidateStatus.CLIP_REQUESTED,
    clip__isnull=False,
    clip__twitch_clip_id__isnull=True,
)
# Written as a union rather than as NOT(_REQUEST_UNKNOWN): negating a condition
# that spans a join is easy to get subtly wrong, and this says what it means.
_REQUESTED = Q(
    status=MomentCandidateStatus.CLIP_REQUESTED,
    clip__twitch_clip_id__isnull=False,
) | Q(status=MomentCandidateStatus.CLIP_REQUESTED, clip__isnull=True)
_CREATED = Q(status=MomentCandidateStatus.CLIP_CREATED)
_FAILED = Q(status=MomentCandidateStatus.FAILED)

# The single source for "which rows are in this display state". Counting and
# filtering read the same predicates, so a filtered list can never disagree with
# the count on the card above it.
CLIP_STATE_FILTERS: dict[str, Q] = {
    state.NOT_REQUESTED: _NOT_REQUESTED,
    state.REQUESTED: _REQUESTED,
    state.REQUEST_UNKNOWN: _REQUEST_UNKNOWN,
    state.CREATED: _CREATED,
    state.FAILED: _FAILED,
}


# "A clip that Twitch is confirmed to have created."
#
# Deliberately stricter than "the moment has a Clip row". M6 writes the local
# row *before* the external request, precisely so it can represent a request
# whose outcome is not yet known, so the row's existence proves only that
# ClipperStash asked. Verification is what proves the clip exists: the
# candidate advanced to CLIP_CREATED, Twitch returned an id, and Get Clips
# confirmed it at `ready_at`. All three are required together — each is written
# by the same verification step, so a row satisfying one but not the others
# would mean the invariant had been broken elsewhere, and this feed should show
# nothing in that case.
VERIFIED_CLIP = Q(
    status=MomentCandidateStatus.CLIP_CREATED,
    clip__twitch_clip_id__isnull=False,
    clip__ready_at__isnull=False,
)


def verified_clip_moments(moments: QuerySet[MomentCandidate]) -> QuerySet[MomentCandidate]:
    """Only moments whose clip Twitch is confirmed to have created.

    Ordered by when ClipperStash confirmed the clip, newest first. `ready_at` is
    non-null by the filter above, so PostgreSQL's nulls-first descending sort
    cannot float an unconfirmed row to the top — there are none to float.
    """
    return moments.filter(VERIFIED_CLIP).order_by("-clip__ready_at", "-id")


def moment_queryset() -> QuerySet[MomentCandidate]:
    """Moments with everything the moment serializers read.

    `clip` is a reverse one-to-one, so without selecting it every row would ask
    the database again for its clip state.
    """
    return MomentCandidate.objects.select_related("clip", "session", "session__streamer")


def session_queryset() -> QuerySet[StreamSession]:
    """Sessions with their streamer and their moment tallies.

    Both tallies count rows of the same join, so they cannot multiply each
    other; `distinct=True` keeps that true if a second join is ever added.
    """
    return (
        StreamSession.objects.select_related("streamer")
        .annotate(
            moment_count=Count("moment_candidates", distinct=True),
            clip_created_count=Count(
                "moment_candidates",
                filter=Q(moment_candidates__status=MomentCandidateStatus.CLIP_CREATED),
                distinct=True,
            ),
        )
        .order_by("-started_at", "-id")
    )


def moment_counts(moments: QuerySet[MomentCandidate]) -> dict[str, int]:
    """Partition a moment queryset by display clip state, in one statement."""
    aggregates = moments.aggregate(
        total=Count("id"),
        **{name: Count("id", filter=predicate) for name, predicate in CLIP_STATE_FILTERS.items()},
    )
    return {key: value or 0 for key, value in aggregates.items()}


def session_counts(session: StreamSession) -> dict[str, int]:
    """Everything the session detail page counts.

    Chat is reported as a total only. The dashboard never reads message rows:
    an operator needs to know how much was said, not what was said.
    """
    counts = moment_counts(MomentCandidate.objects.filter(session=session))
    counts["chat_messages"] = ChatMessage.objects.filter(session=session).count()
    return counts


def overview_counts() -> dict[str, dict[str, int]]:
    """The whole-installation tallies, as three statements."""
    streamers = Streamer.objects.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(is_active=True)),
    )
    sessions = StreamSession.objects.aggregate(
        total=Count("id"),
        live=Count("id", filter=Q(status=StreamSessionStatus.LIVE)),
        ended=Count("id", filter=Q(status=StreamSessionStatus.ENDED)),
    )
    return {
        "streamers": {key: value or 0 for key, value in streamers.items()},
        "sessions": {key: value or 0 for key, value in sessions.items()},
        "moments": moment_counts(MomentCandidate.objects.all()),
    }
