"""Derived presentation state for a moment's clip.

The persisted statuses stay exactly as the pipeline writes them. This is the
read layer's own vocabulary, computed from what is already stored, so the
dashboard can distinguish "no clip was ever asked for" from "a clip was asked
for and we do not know what happened" without any new column.
"""

from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist

from apps.clips.models import Clip
from apps.moments.models import MomentCandidate, MomentCandidateStatus

# Display states. Not persisted anywhere and never written back.
NOT_REQUESTED = "not_requested"
REQUESTED = "requested"
REQUEST_UNKNOWN = "request_unknown"
CREATED = "created"
FAILED = "failed"

CLIP_STATES = (NOT_REQUESTED, REQUESTED, REQUEST_UNKNOWN, CREATED, FAILED)


def clip_for(candidate: MomentCandidate) -> Clip | None:
    """The candidate's clip, or None.

    Reads the reverse one-to-one, so callers listing candidates must have
    selected it; otherwise this issues a query per candidate.
    """
    try:
        return candidate.clip
    except ObjectDoesNotExist:
        return None


def derive_clip_state(candidate: MomentCandidate) -> str:
    """Map a candidate and its clip, if any, onto one display state.

    The interesting case is `REQUEST_UNKNOWN`: a claimed clip with no Twitch id
    means the request's outcome was never learned. It is deliberately not shown
    as a failure — nothing established that the clip does not exist.
    """
    if candidate.status == MomentCandidateStatus.CLIP_CREATED:
        return CREATED
    if candidate.status == MomentCandidateStatus.FAILED:
        return FAILED

    if candidate.status == MomentCandidateStatus.CLIP_REQUESTED:
        clip = clip_for(candidate)
        if clip is not None and not clip.twitch_clip_id:
            return REQUEST_UNKNOWN
        return REQUESTED

    return NOT_REQUESTED
