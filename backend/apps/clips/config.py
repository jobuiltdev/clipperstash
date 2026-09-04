"""Tunables for live clip creation.

Centralized so a timing decision can be reconsidered in one place rather than
hunted through the service.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClipConfig:
    """One calibration for the clip request and verification cycle."""

    # How long to keep asking Twitch whether the clip exists before giving up.
    #
    # Twitch's own documentation disagrees with itself here: the Clips guide
    # says to assume failure after 15 seconds, while the API reference says 60.
    # The larger bound is used deliberately — declaring failure early would
    # abandon a clip that Twitch was still assembling, and the cost of waiting
    # is only a slower manual command.
    verification_timeout_seconds: float = 60.0
    verification_poll_seconds: float = 2.0

    # How old a candidate may be before a *live* clip request is refused.
    #
    # Live Create Clip captures whatever is airing when the request arrives; it
    # cannot reach back to the moment that was detected. The detector's current
    # window already looks ten seconds back, so a short budget keeps the clip
    # close to what was actually detected. Anything older would clip an
    # unrelated part of the stream while claiming to be that moment.
    max_candidate_age_seconds: float = 15.0

    # A candidate timestamped slightly ahead of us is clock skew, not a bug.
    # Beyond this it is meaningless and refused.
    max_candidate_clock_skew_seconds: float = 2.0


DEFAULT_CONFIG = ClipConfig()
