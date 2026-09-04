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


class ChatMessage(models.Model):
    """One chat message observed during a broadcast.

    Deliberately narrow. The upcoming moment detector needs *when* people
    reacted, *how many distinct people* reacted, how emote-heavy the reaction
    was, and the reaction language itself — not a viewer database. So this row
    holds a pseudonymous chatter hash, the text, a timestamp and an emote count,
    and nothing else. Badges, colours, profile data, subscription and reward
    metadata, cheermotes and the raw EventSub payload are all discarded during
    normalization rather than stored.

    Retention: this is short-lived operational data for detection, not durable
    user history. Automatic expiry is **not** implemented yet; a later hardening
    milestone will add it. Until then nothing deletes these rows on its own.
    """

    session = models.ForeignKey(
        StreamSession,
        on_delete=models.CASCADE,
        related_name="chat_messages",
    )

    # Twitch's EventSub delivery identifier. Delivery is at-least-once, so this
    # is the column that makes a repeated notification harmless.
    twitch_event_message_id = models.CharField(max_length=128, unique=True)
    # Twitch chat's own identifier for the message itself.
    twitch_message_id = models.CharField(max_length=128, unique=True)

    # A keyed HMAC of the chatter's Twitch user id. The id itself is never
    # stored, so distinct chatters can be counted without keeping identities.
    chatter_user_id_hash = models.CharField(max_length=64, db_index=True)

    text = models.TextField(blank=True)
    # When Twitch says the notification was produced, always timezone-aware UTC.
    timestamp = models.DateTimeField()
    emote_count = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("timestamp",)
        indexes = [
            # The shape the detector will read: one session, ordered by time.
            models.Index(fields=("session", "timestamp"), name="chat_session_time_idx"),
        ]

    def __str__(self) -> str:
        return f"Chat message {self.twitch_message_id} in session {self.session_id}"

    def __repr__(self) -> str:
        # Never include message text or the chatter hash in debugging output.
        return (
            f"<ChatMessage twitch_message_id={self.twitch_message_id!r} "
            f"session_id={self.session_id!r}>"
        )
