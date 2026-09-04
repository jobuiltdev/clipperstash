"""Persistence for observed broadcasts.

A `StreamSession` is one broadcast, identified by the platform's own stream id.
It is created when ClipperStash first observes the broadcaster live and closed
when ClipperStash observes them offline.

`Streamer` deliberately carries no live/offline flag: an active session *is* the
representation of an active broadcast, and its absence is the representation of
an idle channel.
"""

from __future__ import annotations

from django.db import models

from apps.streamers.models import Streamer


class StreamSessionStatus(models.TextChoices):
    LIVE = "live", "Live"
    ENDED = "ended", "Ended"


class StreamSession(models.Model):
    """One broadcast, as observed by ClipperStash."""

    streamer = models.ForeignKey(
        Streamer,
        on_delete=models.CASCADE,
        related_name="stream_sessions",
    )
    # The platform's own identity for this broadcast. Two observations that
    # report the same value are the same broadcast.
    platform_stream_id = models.CharField(max_length=64, unique=True)

    # Twitch's authoritative broadcast start. Never derived from the local clock.
    started_at = models.DateTimeField()
    # When ClipperStash *observed* the broadcast to be over. This is not
    # Twitch's exact end timestamp, which is not available from Get Streams:
    # it is only as precise as the interval between observations.
    ended_at = models.DateTimeField(null=True, blank=True)

    title = models.CharField(max_length=512, blank=True)
    category_id = models.CharField(max_length=64, blank=True)
    category_name = models.CharField(max_length=255, blank=True)
    language = models.CharField(max_length=16, blank=True)
    is_mature = models.BooleanField(default=False)

    last_viewer_count = models.PositiveIntegerField(default=0)
    # When ClipperStash last received a successful observation for this session.
    last_observed_at = models.DateTimeField()

    status = models.CharField(
        max_length=16,
        choices=StreamSessionStatus.choices,
        default=StreamSessionStatus.LIVE,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-started_at",)
        constraints = [
            # A streamer can have any number of ended sessions, but only one
            # live one. Enforced by the database, not by application code alone,
            # so two concurrent observations cannot both open a session.
            models.UniqueConstraint(
                fields=("streamer",),
                condition=models.Q(status=StreamSessionStatus.LIVE),
                name="unique_live_session_per_streamer",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.streamer.username} {self.platform_stream_id} ({self.status})"

    def __repr__(self) -> str:
        return (
            f"<StreamSession platform_stream_id={self.platform_stream_id!r} status={self.status!r}>"
        )

    @property
    def is_live(self) -> bool:
        return self.status == StreamSessionStatus.LIVE
