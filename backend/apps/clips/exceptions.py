"""Typed errors for clip creation.

Two families, kept apart deliberately:

* **Preconditions** — the request should never have been attempted. Nothing was
  claimed, no Twitch call was made, and the candidate is left exactly as it was.
  A stale candidate is not a broken one.
* **Failures after the claim** — clipping was under way and did not succeed.
  These do mark the candidate `FAILED`, with a short controlled reason.
"""


class ClipError(Exception):
    """Base class for clip-domain failures."""

    code = "clip_error"
    message = "The clip could not be created."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message


class ClipPreconditionError(ClipError):
    """The candidate was not in a state where clipping makes sense.

    Never marks the candidate `FAILED`: nothing was attempted, so nothing
    failed.
    """


class StaleCandidateError(ClipPreconditionError):
    """The moment is too old for a live clip.

    Live Create Clip captures the present, so clipping a stale candidate would
    capture an unrelated part of the stream while claiming to be that moment.
    Historical moments belong to the separately authorized VOD path, which is
    not implemented.
    """

    code = "stale_moment_candidate"
    message = "That moment is too old to clip live."


class SessionNotLiveError(ClipPreconditionError):
    """The broadcast this moment belongs to is over."""

    code = "stream_not_live"
    message = "That stream session is no longer live."


class CandidateNotEligibleError(ClipPreconditionError):
    """The candidate is not in a state that may begin clipping."""

    code = "candidate_not_eligible"
    message = "That moment is not awaiting a clip."


class ClipAuthorizationError(ClipPreconditionError):
    """No connected Twitch account can create clips.

    Either nothing is connected, or the connection lacks `clips:edit`.
    """

    code = "clip_not_authorized"
    message = "Reconnect the Twitch account to grant clip access."


class ClipRequestStateUnknown(ClipPreconditionError):
    """Clipping was claimed, but whether Twitch received the request is unknown.

    Reached when a clip has been claimed and no Twitch clip id was ever
    recorded — because the process stopped around the request, or because the
    request failed in a way that cannot distinguish "never arrived" from
    "arrived and was accepted".

    Twitch's Create Clip takes no caller-supplied idempotency key, so there is
    no way to ask "did you already do this for me?". Re-sending could therefore
    create a second clip of an unrelated part of the stream. ClipperStash
    refuses to guess: the claim, the candidate's `CLIP_REQUESTED` status and the
    empty clip row are all left in place, and resolving them is left to a future
    operator-controlled policy.

    Treated as a precondition rather than a failure, because the outcome is not
    known to be a failure.
    """

    code = "clip_request_state_unknown"
    message = (
        "The clip request's outcome is unknown; no Twitch clip id was recorded, "
        "so it cannot be verified and is not retried automatically."
    )


class ClipRequestFailed(ClipError):
    """Clipping was claimed and then did not succeed.

    Carries one of the controlled `ClipFailureCode` values.
    """

    def __init__(self, failure_code: str, message: str) -> None:
        super().__init__(message)
        self.code = failure_code
