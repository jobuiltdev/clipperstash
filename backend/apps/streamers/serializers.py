"""Public representations for the streamers API."""

from __future__ import annotations

from rest_framework import serializers

from apps.streamers.models import Streamer


class ResolveStreamerRequestSerializer(serializers.Serializer):
    """The resolve request body.

    Validation beyond "a non-empty string arrived" is left to the parser, which
    owns Twitch input semantics and produces the user-facing message.
    """

    input = serializers.CharField(allow_blank=True, trim_whitespace=False)


class StreamerSerializer(serializers.ModelSerializer):
    """Safe public view of a streamer.

    The field list is explicit rather than `__all__`, so a column added later is
    never published by accident.
    """

    class Meta:
        model = Streamer
        fields = (
            "id",
            "platform",
            "platform_user_id",
            "username",
            "display_name",
            "channel_url",
            "profile_image_url",
            "broadcaster_type",
            "description",
        )
        read_only_fields = fields
