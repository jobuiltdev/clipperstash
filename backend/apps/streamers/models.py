"""Persistence for resolved streamer identities.

A `Streamer` records who a channel is, not what it is doing. Live state — whether
the channel is broadcasting, its title, category, viewer or follower counts — is
deliberately absent: that belongs to stream monitoring in a later milestone.
"""

from __future__ import annotations

from django.db import models


class Platform(models.TextChoices):
    TWITCH = "twitch", "Twitch"


class Streamer(models.Model):
    """A channel ClipperStash has resolved against its platform."""

    platform = models.CharField(
        max_length=32,
        choices=Platform.choices,
        default=Platform.TWITCH,
    )
    # The platform's own stable account id. This, not the username, is the
    # identity anchor: a channel can be renamed, but this does not change.
    platform_user_id = models.CharField(max_length=64)
    username = models.CharField(max_length=64)
    display_name = models.CharField(max_length=255, blank=True)
    channel_url = models.URLField(max_length=500)
    profile_image_url = models.URLField(max_length=500, blank=True)
    broadcaster_type = models.CharField(max_length=32, blank=True)
    description = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("username",)
        constraints = [
            models.UniqueConstraint(
                fields=("platform", "platform_user_id"),
                name="unique_streamer_platform_account",
            ),
            models.UniqueConstraint(
                fields=("platform", "username"),
                name="unique_streamer_platform_username",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.display_name or self.username} ({self.platform})"

    def __repr__(self) -> str:
        return (
            f"<Streamer platform={self.platform!r} "
            f"platform_user_id={self.platform_user_id!r} username={self.username!r}>"
        )
