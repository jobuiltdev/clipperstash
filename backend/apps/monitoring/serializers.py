"""Public representations for observation results."""

from __future__ import annotations

from rest_framework import serializers

from apps.monitoring.models import StreamSession
from apps.streamers.models import Streamer


class ObservedStreamerSerializer(serializers.ModelSerializer):
    """The minimum needed to identify which channel was observed."""

    class Meta:
        model = Streamer
        fields = ("id", "username", "display_name")
        read_only_fields = fields


class ObservedStreamSerializer(serializers.ModelSerializer):
    """The live-stream half of an observation.

    The field list is explicit, so a column added to `StreamSession` later is
    never published by accident.
    """

    session_id = serializers.IntegerField(source="id", read_only=True)
    viewer_count = serializers.IntegerField(source="last_viewer_count", read_only=True)

    class Meta:
        model = StreamSession
        fields = (
            "session_id",
            "platform_stream_id",
            "started_at",
            "title",
            "category_id",
            "category_name",
            "language",
            "viewer_count",
            "is_mature",
        )
        read_only_fields = fields
