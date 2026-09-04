"""Requesting and verifying live Twitch clips.

The shape of one attempt:

1. check the preconditions — freshness, session liveness, candidate state,
   Twitch capability — before anything is claimed or sent;
2. claim the candidate in a short transaction, which is what stops two callers
   both asking Twitch for the same moment;
3. leave the transaction, then ask Twitch;
4. poll until Twitch confirms the clip exists, or the deadline passes.

The database transaction is closed before any HTTP happens. Holding one open
across a minute of polling would pin a connection and block anything else
touching the row.

**The limit of that guarantee.** Twitch's Create Clip accepts no
caller-supplied idempotency key, so ClipperStash cannot ask "have you already
done this for me?". The claim makes two *concurrent* callers safe, but there is
an unavoidable window between the claim committing and the returned clip id
reaching the database. If execution stops inside it — a crash, a lost machine, a
connection whose outcome cannot be known — the row says a request was claimed
while nothing records whether Twitch received one.

ClipperStash guarantees one local claim per moment, and that it will never
automatically send a second request from that indeterminate state. It cannot
guarantee that Twitch did or did not act on the first. Re-sending would risk a
duplicate clip of an unrelated part of the stream, so the state is surfaced as
`clip_request_state_unknown` and left intact for a future operator-controlled
policy to resolve.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from apps.clips.config import DEFAULT_CONFIG, ClipConfig
from apps.clips.exceptions import (
    CandidateNotEligibleError,
    ClipAuthorizationError,
    ClipRequestFailed,
    ClipRequestStateUnknown,
    SessionNotLiveError,
    StaleCandidateError,
)
from apps.clips.models import Clip, ClipFailureCode
from apps.moments.models import MomentCandidate, MomentCandidateStatus
from apps.monitoring.models import StreamSession, StreamSessionStatus
from apps.twitch import services as twitch_services
from apps.twitch.client import TwitchClient
from apps.twitch.exceptions import (
    TwitchAPIError,
    TwitchAuthenticationError,
    TwitchTransportError,
)
from apps.twitch.models import TwitchConnection

logger = logging.getLogger(__name__)

CLIPS_EDIT_SCOPE = "clips:edit"


@dataclass(frozen=True)
class ClipResult:
    """The outcome of one attempt, in terms safe to print or log."""

    clip: Clip
    candidate: MomentCandidate
    created: bool = False
    resumed: bool = False
    already_ready: bool = False
    attempts: int = 0


# ============================================================ preconditions ===


def require_clip_connection() -> TwitchConnection:
    """The connected Twitch account, if it may create clips.

    Checked before anything is claimed, so a connection that cannot clip never
    leaves a half-started candidate behind.
    """
    connection = TwitchConnection.objects.current()
    if connection is None:
        raise ClipAuthorizationError("No Twitch account is connected.")
    if connection.requires_reauthorization or CLIPS_EDIT_SCOPE not in connection.granted_scopes():
        raise ClipAuthorizationError()
    return connection


def check_freshness(
    candidate: MomentCandidate,
    *,
    now: datetime,
    config: ClipConfig = DEFAULT_CONFIG,
) -> None:
    """Refuse a moment too old — or too far ahead — to clip live.

    Age is inclusive at the limit: exactly `max_candidate_age_seconds` old is
    still allowed. A candidate slightly in the future is treated as clock skew
    and tolerated; further ahead than that is meaningless and refused.
    """
    age = (now - candidate.detected_at).total_seconds()

    if age < -config.max_candidate_clock_skew_seconds:
        raise StaleCandidateError("That moment is timestamped in the future.")
    if age > config.max_candidate_age_seconds:
        raise StaleCandidateError()


def check_session_live(candidate: MomentCandidate) -> StreamSession:
    """Refuse a moment whose broadcast has ended.

    The session's current status is read from the database rather than trusted
    from whatever was loaded earlier. Stream lifecycle stays entirely owned by
    stream observation: nothing here asks Twitch, and nothing here changes it.
    """
    session = StreamSession.objects.filter(pk=candidate.session_id).first()
    if session is None or session.status != StreamSessionStatus.LIVE:
        raise SessionNotLiveError()
    return session


# =================================================================== claim ====


@transaction.atomic
def claim_candidate(
    candidate: MomentCandidate,
    *,
    now: datetime,
) -> tuple[MomentCandidate, Clip, bool]:
    """Take exclusive ownership of clipping this moment.

    Locks the candidate row, re-reads its state under that lock, and moves it
    `DETECTED -> CLIP_REQUESTED` while creating its one `Clip`. A second caller
    arriving concurrently blocks here, then sees `CLIP_REQUESTED` and knows the
    work is already under way rather than sending its own request.

    Returns `(candidate, clip, newly_claimed)`. The transaction ends when this
    returns — Twitch is contacted afterwards, never while holding the lock.
    """
    locked = MomentCandidate.objects.select_for_update().get(pk=candidate.pk)

    if locked.status == MomentCandidateStatus.DETECTED:
        clip = Clip.objects.create(moment=locked, requested_at=now)
        locked.status = MomentCandidateStatus.CLIP_REQUESTED
        locked.save(update_fields=["status", "updated_at"])
        logger.info("Claimed moment %s for clipping.", locked.pk)
        return locked, clip, True

    existing = Clip.objects.filter(moment=locked).first()
    if existing is None:
        # Any non-DETECTED status without a clip means this candidate is not
        # awaiting one: rejected, failed before a claim, or otherwise finished.
        raise CandidateNotEligibleError()
    return locked, existing, False


# ============================================================ verification ====


def verify_clip(
    clip: Clip,
    connection: TwitchConnection,
    *,
    client: TwitchClient | None = None,
    config: ClipConfig = DEFAULT_CONFIG,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> ClipResult:
    """Poll Twitch until the clip exists, or the deadline passes.

    Elapsed time is measured on the monotonic clock, so a system clock
    adjustment mid-poll cannot shorten or extend the deadline. The clock and the
    sleeper are injected so tests can run the whole cycle instantly.

    An empty result means "not ready yet", never "failed": clip creation is
    asynchronous and absence early on is expected.
    """
    # Resolved here rather than defaulted at import time, so the clock and the
    # sleeper are always the ones in force at the moment of the call.
    monotonic = monotonic or time.monotonic
    sleep = sleep or time.sleep

    if clip.is_ready:
        return ClipResult(clip=clip, candidate=clip.moment, already_ready=True)

    if not clip.twitch_clip_id:
        # Nothing to ask Twitch about. This is not a failure — the request may
        # well have been accepted — so it must not mark the candidate FAILED,
        # and Get Clips is never called without an id.
        raise ClipRequestStateUnknown()

    client = client or TwitchClient()
    deadline = monotonic() + config.verification_timeout_seconds
    attempts = 0

    while True:
        attempts += 1
        try:
            found = twitch_services.get_clip(connection, clip.twitch_clip_id, client=client)
        except TwitchAuthenticationError as exc:
            raise ClipRequestFailed(
                ClipFailureCode.TWITCH_AUTH_FAILED,
                "Twitch would not authorize the clip lookup.",
            ) from exc
        except TwitchAPIError as exc:
            raise ClipRequestFailed(
                ClipFailureCode.VERIFICATION_FAILED,
                "Twitch could not confirm the clip.",
            ) from exc

        if found is not None:
            expected = clip.moment.session.streamer.platform_user_id
            if found.broadcaster_id != expected:
                # Never attach another channel's clip to this moment.
                raise ClipRequestFailed(
                    ClipFailureCode.BROADCASTER_MISMATCH,
                    "Twitch returned a clip for a different channel.",
                )
            candidate = _record_ready(clip, found)
            logger.info(
                "Clip %s confirmed for moment %s after %s attempt(s).",
                clip.twitch_clip_id,
                clip.moment_id,
                attempts,
            )
            return ClipResult(clip=clip, candidate=candidate, attempts=attempts)

        if monotonic() >= deadline:
            logger.warning(
                "Clip %s for moment %s did not appear within %ss.",
                clip.twitch_clip_id,
                clip.moment_id,
                config.verification_timeout_seconds,
            )
            raise ClipRequestFailed(
                ClipFailureCode.VERIFICATION_TIMEOUT,
                "Twitch did not confirm the clip in time.",
            )

        sleep(config.verification_poll_seconds)


@transaction.atomic
def _record_ready(clip: Clip, found) -> MomentCandidate:
    """Store the confirmed clip and finish the candidate."""
    clip.twitch_url = found.url
    clip.title = found.title
    clip.duration = found.duration
    clip.thumbnail_url = found.thumbnail_url
    clip.twitch_created_at = found.created_at
    clip.ready_at = timezone.now()
    clip.failure_code = ""
    clip.failure_detail = ""
    clip.save()

    candidate = clip.moment
    candidate.status = MomentCandidateStatus.CLIP_CREATED
    candidate.save(update_fields=["status", "updated_at"])
    return candidate


@transaction.atomic
def _record_unknown(clip: Clip) -> None:
    """Note that the request's outcome could not be determined.

    Records the code only. The candidate deliberately stays `CLIP_REQUESTED`:
    marking it FAILED would assert that no clip exists, which is exactly what is
    not known. Nothing is deleted and the clip id stays null.
    """
    clip.failure_code = ClipFailureCode.REQUEST_STATE_UNKNOWN
    clip.failure_detail = ClipRequestStateUnknown.message[:255]
    clip.save(update_fields=["failure_code", "failure_detail", "updated_at"])
    logger.warning(
        "Clip request outcome for moment %s is unknown; not retrying automatically.",
        clip.moment_id,
    )


@transaction.atomic
def _record_failure(clip: Clip, failure: ClipRequestFailed) -> MomentCandidate:
    """Record a post-claim failure, keeping anything useful for recovery.

    The Twitch clip id is deliberately preserved: a clip that appeared just
    after the deadline can still be found by hand later.
    """
    clip.failure_code = failure.code
    clip.failure_detail = failure.message[:255]
    clip.save(update_fields=["failure_code", "failure_detail", "updated_at"])

    candidate = clip.moment
    candidate.status = MomentCandidateStatus.FAILED
    candidate.save(update_fields=["status", "updated_at"])
    logger.warning("Clipping moment %s failed (%s).", candidate.pk, failure.code)
    return candidate


# ============================================================ orchestration ===


def request_clip(
    candidate: MomentCandidate,
    *,
    now: datetime | None = None,
    client: TwitchClient | None = None,
    config: ClipConfig = DEFAULT_CONFIG,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    verify_only: bool = False,
) -> ClipResult:
    """Create and confirm a clip for one detected moment.

    Idempotent by design. An already-confirmed candidate returns its existing
    clip untouched; one that was claimed and interrupted resumes verification
    without sending a second request; only a `DETECTED` candidate ever causes a
    new one.
    """
    now = now or timezone.now()
    monotonic = monotonic or time.monotonic
    sleep = sleep or time.sleep

    existing = Clip.objects.filter(moment=candidate).select_related("moment").first()
    if existing is not None and existing.is_ready:
        return ClipResult(clip=existing, candidate=candidate, already_ready=True)

    # A claim exists but carries no clip id: the outcome of that request is
    # unknowable. Refused here, before anything is claimed again and before any
    # Twitch call, so no second request can ever be sent from this state.
    if existing is not None and not existing.twitch_clip_id:
        raise ClipRequestStateUnknown()

    connection = require_clip_connection()

    # A resumable claim skips the freshness check: the moment was fresh when it
    # was claimed, and the request Twitch already accepted is the one being
    # confirmed. Re-checking would strand a clip that exists.
    resuming = existing is not None and bool(existing.twitch_clip_id)
    if not resuming:
        if verify_only:
            raise CandidateNotEligibleError("There is no clip request to verify.")
        check_freshness(candidate, now=now, config=config)
        check_session_live(candidate)

    candidate, clip, newly_claimed = claim_candidate(candidate, now=now)
    clip.refresh_from_db()

    try:
        if not clip.twitch_clip_id:
            if verify_only:
                raise CandidateNotEligibleError("There is no clip request to verify.")
            _send_request(clip, candidate, connection, client=client)
        result = verify_clip(
            clip,
            connection,
            client=client,
            config=config,
            monotonic=monotonic,
            sleep=sleep,
        )
    except ClipRequestStateUnknown:
        # The claim, the status and the empty clip row all stay exactly as they
        # are, so a later operator policy has everything it needs.
        _record_unknown(clip)
        raise
    except ClipRequestFailed as failure:
        candidate = _record_failure(clip, failure)
        raise

    return ClipResult(
        clip=result.clip,
        candidate=result.candidate,
        created=newly_claimed,
        resumed=not newly_claimed,
        attempts=result.attempts,
    )


def _send_request(
    clip: Clip,
    candidate: MomentCandidate,
    connection: TwitchConnection,
    *,
    client: TwitchClient | None,
) -> None:
    """Ask Twitch for the clip and record the id it hands back.

    A 202 means accepted, not created, so nothing about the candidate advances
    here beyond storing the id to verify.

    Sent exactly once. The only automatic retry anywhere in this path is the
    bounded token refresh on a definitive 401, which is safe precisely because
    Twitch rejected the first request outright. No other outcome — including a
    timeout — causes a second request.
    """
    broadcaster_id = candidate.session.streamer.platform_user_id

    try:
        requested = twitch_services.create_clip(
            connection, broadcaster_id=broadcaster_id, client=client
        )
    except TwitchTransportError as exc:
        # The request may have arrived, been acted on, or never left. Nothing
        # here can tell which, so the outcome is unknown rather than failed and
        # no second request is sent.
        raise ClipRequestStateUnknown() from exc
    except TwitchAuthenticationError as exc:
        # Twitch answered, definitively refusing the credentials.
        raise ClipRequestFailed(
            ClipFailureCode.TWITCH_AUTH_FAILED,
            "Twitch would not authorize the clip request.",
        ) from exc
    except TwitchAPIError as exc:
        # Twitch answered, so the outcome is known. Its own error text is never
        # echoed; only the status distinguishes a refusal from an answer that
        # arrived but could not be read.
        code = (
            ClipFailureCode.TWITCH_CREATE_MALFORMED
            if exc.status_code is None
            else ClipFailureCode.TWITCH_CREATE_REJECTED
        )
        raise ClipRequestFailed(code, "Twitch did not accept the clip request.") from exc

    clip.twitch_clip_id = requested.clip_id
    clip.save(update_fields=["twitch_clip_id", "updated_at"])
    logger.info(
        "Requested clip %s for moment %s (broadcaster_user_id=%s).",
        requested.clip_id,
        candidate.pk,
        broadcaster_id,
    )


def recent_candidate_age(candidate: MomentCandidate, *, now: datetime | None = None) -> timedelta:
    """How old a candidate is, for reporting."""
    return (now or timezone.now()) - candidate.detected_at
