"""Persistence for clips requested from Twitch.

A `Clip` is both the record of a clip and the claim that stops a second request
being made for the same moment. It is created the instant clipping is claimed,
before Twitch is contacted, and filled in as the request is accepted and then
verified.

It holds only what Twitch told us about the clip. No token, no raw response, no
chat content, no chatter identity — and deliberately not the `edit_url` Twitch
returns, which is a browser convenience ClipperStash neither uses nor keeps.
"""

from __future__ import annotations

from django.db import models

from apps.moments.models import MomentCandidate


class ClipFailureCode(models.TextChoices):
    """Why a claimed clip did not end up existing, or could not be resolved.

    A short controlled vocabulary rather than an exception blob: enough to know
    what happened and whether it is worth retrying, with nothing from Twitch's
    response body carried along. One member — `REQUEST_STATE_UNKNOWN` — records
    an unresolved outcome rather than a failure; the candidate stays
    `CLIP_REQUESTED` in that case.
    """

    TWITCH_CREATE_REJECTED = "twitch_create_rejected", "Twitch rejected the clip request"
    TWITCH_AUTH_FAILED = "twitch_auth_failed", "Twitch would not authorize the request"
    TWITCH_CREATE_MALFORMED = "twitch_create_malformed", "Twitch returned an unusable response"
    VERIFICATION_TIMEOUT = "verification_timeout", "The clip did not appear in time"
    VERIFICATION_FAILED = "verification_failed", "The clip could not be verified"
    BROADCASTER_MISMATCH = "broadcaster_mismatch", "Twitch returned another channel's clip"
    # Not a failure: the outcome is genuinely unknown. Recorded so the state is
    # visible without inferring it from a null clip id.
    REQUEST_STATE_UNKNOWN = "request_state_unknown", "The request's outcome is unknown"


class Clip(models.Model):
    """One clip request against one detected moment."""

    moment = models.OneToOneField(
        MomentCandidate,
        # A clip outlives the analysis that prompted it, so a candidate cannot
        # be removed out from under its clip.
        on_delete=models.PROTECT,
        related_name="clip",
    )

    # Null until Twitch accepts the request. Unique once set, so the same clip
    # can never be recorded twice; NULLs do not collide under the constraint,
    # which lets the row exist as a claim before there is anything to identify.
    twitch_clip_id = models.CharField(max_length=128, null=True, blank=True, unique=True)

    # Everything below stays empty until Get Clips confirms the clip exists.
    # The URL is Twitch's own; it is never assembled from the id.
    twitch_url = models.URLField(max_length=500, blank=True)
    title = models.CharField(max_length=255, blank=True)
    duration = models.FloatField(null=True, blank=True)
    thumbnail_url = models.URLField(max_length=500, blank=True)
    twitch_created_at = models.DateTimeField(null=True, blank=True)

    # When ClipperStash claimed the moment and asked for the clip.
    requested_at = models.DateTimeField()
    # When ClipperStash first saw Twitch confirm the clip. Not Twitch's own
    # creation time, which is `twitch_created_at`.
    ready_at = models.DateTimeField(null=True, blank=True)

    failure_code = models.CharField(
        max_length=32,
        choices=ClipFailureCode.choices,
        blank=True,
        default="",
    )
    # A short, sanitized note. Never a Twitch response body.
    failure_detail = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-requested_at",)

    def __str__(self) -> str:
        return f"Clip {self.twitch_clip_id or '(unrequested)'} for moment {self.moment_id}"

    def __repr__(self) -> str:
        return (
            f"<Clip moment_id={self.moment_id!r} twitch_clip_id={self.twitch_clip_id!r} "
            f"ready={self.is_ready!r}>"
        )

    @property
    def is_ready(self) -> bool:
        """Whether Twitch has confirmed the clip exists."""
        return self.ready_at is not None
