"""Persistence for detected moments.

A `MomentCandidate` is a finding: a window of chat the detector judged unusually
clip-worthy, stored with the full working that produced the judgement. It holds
aggregates only — no message text, no message ids, no chatter identities, no raw
Twitch payload — so a candidate can be reviewed and recalibrated without
carrying chat content forward.
"""

from __future__ import annotations

from django.db import models

from apps.monitoring.models import StreamSession


class MomentCandidateStatus(models.TextChoices):
    """Lifecycle of a candidate.

    Only `DETECTED` is ever produced today. The clip-related members exist so
    the column does not need migrating when clip creation arrives; nothing in
    this milestone transitions to them, and no clip is created.
    """

    DETECTED = "detected", "Detected"
    CLIP_REQUESTED = "clip_requested", "Clip requested"
    CLIP_CREATED = "clip_created", "Clip created"
    REJECTED = "rejected", "Rejected"
    FAILED = "failed", "Failed"


class MomentCandidate(models.Model):
    """One window the detector considered worth clipping."""

    session = models.ForeignKey(
        StreamSession,
        # A finding outlives the operational chat it was derived from, so a
        # session cannot be removed out from under its detection history.
        on_delete=models.PROTECT,
        related_name="moment_candidates",
    )

    # The evaluation instant, which is also the current window's end. Supplied
    # by the caller, so a replayed historical evaluation records the moment it
    # is describing rather than the moment it was run.
    detected_at = models.DateTimeField()

    current_window_start = models.DateTimeField()
    current_window_end = models.DateTimeField()
    baseline_window_start = models.DateTimeField()
    baseline_window_end = models.DateTimeField()

    # -- raw metrics ---------------------------------------------------------
    current_message_count = models.PositiveIntegerField()
    baseline_message_count = models.PositiveIntegerField()
    current_unique_chatters = models.PositiveIntegerField()
    baseline_unique_chatters = models.PositiveIntegerField()
    current_emote_count = models.PositiveIntegerField()
    baseline_emote_count = models.PositiveIntegerField()
    current_reaction_count = models.PositiveIntegerField()
    baseline_reaction_count = models.PositiveIntegerField()
    velocity_ratio = models.FloatField()

    # -- component scores, each 0.0-1.0 --------------------------------------
    velocity_score = models.FloatField()
    reaction_score = models.FloatField()
    emote_score = models.FloatField()
    diversity_score = models.FloatField()
    absolute_activity_score = models.FloatField()

    # -- overall, on the published 0-100 scale -------------------------------
    total_score = models.FloatField()

    status = models.CharField(
        max_length=32,
        choices=MomentCandidateStatus.choices,
        default=MomentCandidateStatus.DETECTED,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-detected_at",)
        indexes = [
            # The shape cooldown checks and review will read: one session,
            # most recent first.
            models.Index(fields=("session", "detected_at"), name="moment_session_time_idx"),
        ]

    def __str__(self) -> str:
        return f"Moment {self.total_score:.1f} in session {self.session_id} at {self.detected_at}"

    def __repr__(self) -> str:
        return (
            f"<MomentCandidate session_id={self.session_id!r} "
            f"total_score={self.total_score!r} status={self.status!r}>"
        )
