"""Read-only representations for the operator dashboard.

Every serializer here is an explicit allowlist. The pipeline stores things the
dashboard must never publish — chat text, chatter hashes, Twitch message ids,
tokens — so field lists are written out rather than derived, and a column added
later is never exposed by accident.

Nothing in this module writes. There is no `create`, no `update`, and no
serializer is ever fed request data.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.clips.config import DEFAULT_CONFIG as CLIP_CONFIG
from apps.clips.models import Clip
from apps.dashboard.state import clip_for, derive_clip_state
from apps.moments.detector import DEFAULT_CONFIG as DETECTOR_CONFIG
from apps.moments.models import MomentCandidate
from apps.monitoring.models import StreamSession
from apps.streamers.models import Streamer


class StreamerRefSerializer(serializers.ModelSerializer):
    """Just enough to say whose stream this is."""

    class Meta:
        model = Streamer
        fields = ("id", "username", "display_name", "channel_url", "profile_image_url")
        read_only_fields = fields


class ClipSerializer(serializers.ModelSerializer):
    """A clip as the dashboard shows it.

    `edit_url` is not in the model at all, and `failure_detail` is the single
    human-readable explanation the pipeline produces — a short controlled
    string, never a Twitch response body.
    """

    class Meta:
        model = Clip
        fields = (
            "id",
            "twitch_clip_id",
            "twitch_url",
            "title",
            "duration",
            "thumbnail_url",
            "twitch_created_at",
            "requested_at",
            "ready_at",
            "failure_code",
            "failure_detail",
        )
        read_only_fields = fields


class MomentSummarySerializer(serializers.ModelSerializer):
    """One row in a session's moment list.

    Carries the full score breakdown, because a list of bare totals cannot be
    reasoned about during calibration.
    """

    clip_state = serializers.SerializerMethodField()
    clip_id = serializers.SerializerMethodField()
    twitch_clip_id = serializers.SerializerMethodField()
    twitch_url = serializers.SerializerMethodField()
    failure_code = serializers.SerializerMethodField()
    requested_at = serializers.SerializerMethodField()
    ready_at = serializers.SerializerMethodField()

    current_unique_chatter_count = serializers.IntegerField(source="current_unique_chatters")
    baseline_unique_chatter_count = serializers.IntegerField(source="baseline_unique_chatters")

    class Meta:
        model = MomentCandidate
        fields = (
            "id",
            "detected_at",
            "status",
            "total_score",
            "velocity_score",
            "reaction_score",
            "emote_score",
            "diversity_score",
            "absolute_activity_score",
            "velocity_ratio",
            "current_message_count",
            "baseline_message_count",
            "current_unique_chatter_count",
            "baseline_unique_chatter_count",
            "current_emote_count",
            "baseline_emote_count",
            "current_reaction_count",
            "baseline_reaction_count",
            "clip_state",
            "clip_id",
            "twitch_clip_id",
            "twitch_url",
            "failure_code",
            "requested_at",
            "ready_at",
        )
        read_only_fields = fields

    def get_clip_state(self, candidate: MomentCandidate) -> str:
        return derive_clip_state(candidate)

    def get_clip_id(self, candidate: MomentCandidate) -> int | None:
        clip = clip_for(candidate)
        return clip.pk if clip else None

    def get_twitch_clip_id(self, candidate: MomentCandidate) -> str | None:
        clip = clip_for(candidate)
        return clip.twitch_clip_id if clip else None

    def get_twitch_url(self, candidate: MomentCandidate) -> str:
        """Only ever the URL Twitch itself returned, and only once verified."""
        clip = clip_for(candidate)
        return clip.twitch_url if clip else ""

    def get_failure_code(self, candidate: MomentCandidate) -> str:
        clip = clip_for(candidate)
        return clip.failure_code if clip else ""

    def get_requested_at(self, candidate: MomentCandidate):
        clip = clip_for(candidate)
        return clip.requested_at if clip else None

    def get_ready_at(self, candidate: MomentCandidate):
        clip = clip_for(candidate)
        return clip.ready_at if clip else None


class MomentFeedSerializer(MomentSummarySerializer):
    """A moment shown away from its own session's page.

    The overview mixes moments from every streamer, so each row has to say
    whose stream it came from; inside a session that context is already on the
    page and would only be repeated.
    """

    session_id = serializers.IntegerField(read_only=True)
    streamer = StreamerRefSerializer(source="session.streamer", read_only=True)
    session_title = serializers.CharField(source="session.title", read_only=True)

    class Meta(MomentSummarySerializer.Meta):
        fields = (
            *MomentSummarySerializer.Meta.fields,
            "session_id",
            "session_title",
            "streamer",
        )
        read_only_fields = fields


class MomentWindowsSerializer(serializers.ModelSerializer):
    """The two windows the score was computed over."""

    baseline_start = serializers.DateTimeField(source="baseline_window_start")
    baseline_end = serializers.DateTimeField(source="baseline_window_end")
    current_start = serializers.DateTimeField(source="current_window_start")
    current_end = serializers.DateTimeField(source="current_window_end")

    class Meta:
        model = MomentCandidate
        fields = ("baseline_start", "baseline_end", "current_start", "current_end")
        read_only_fields = fields


class MomentDetailSerializer(MomentSummarySerializer):
    """The forensic view of one moment.

    Everything needed to explain a score: the components, the raw aggregates
    behind them, the windows they were measured over, the threshold in force,
    and whatever became of the clip.
    """

    streamer = StreamerRefSerializer(source="session.streamer", read_only=True)
    session_id = serializers.IntegerField(read_only=True)
    windows = serializers.SerializerMethodField()
    threshold = serializers.SerializerMethodField()
    failure_detail = serializers.SerializerMethodField()
    clip = serializers.SerializerMethodField()

    class Meta(MomentSummarySerializer.Meta):
        fields = (
            *MomentSummarySerializer.Meta.fields,
            "session_id",
            "streamer",
            "windows",
            "threshold",
            "failure_detail",
            "clip",
        )
        read_only_fields = fields

    def get_windows(self, candidate: MomentCandidate) -> dict:
        return MomentWindowsSerializer(candidate).data

    def get_threshold(self, _candidate: MomentCandidate) -> float:
        """The detector's current threshold, for reference.

        Read from the same centralized configuration the detector uses, so the
        dashboard can never disagree with it.
        """
        return DETECTOR_CONFIG.candidate_threshold

    def get_failure_detail(self, candidate: MomentCandidate) -> str:
        clip = clip_for(candidate)
        return clip.failure_detail if clip else ""

    def get_clip(self, candidate: MomentCandidate) -> dict | None:
        clip = clip_for(candidate)
        return ClipSerializer(clip).data if clip else None


class StreamSessionSummarySerializer(serializers.ModelSerializer):
    """One row in a streamer's session list.

    `moment_count` and `clip_created_count` are annotated by the view in the
    same query, never counted per row.
    """

    streamer = StreamerRefSerializer(read_only=True)
    category = serializers.CharField(source="category_name", read_only=True)
    viewer_count = serializers.IntegerField(source="last_viewer_count", read_only=True)
    moment_count = serializers.IntegerField(read_only=True)
    clip_created_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = StreamSession
        fields = (
            "id",
            "streamer",
            "platform_stream_id",
            "status",
            "started_at",
            "ended_at",
            "title",
            "category",
            "language",
            "viewer_count",
            "last_observed_at",
            "moment_count",
            "clip_created_count",
        )
        read_only_fields = fields


class StreamSessionDetailSerializer(StreamSessionSummarySerializer):
    """A session with its moment breakdown.

    `counts` is supplied by the view from a single aggregate query. Chat is
    never included: the dashboard reasons about aggregates, not transcripts.
    """

    counts = serializers.SerializerMethodField()

    class Meta(StreamSessionSummarySerializer.Meta):
        fields = (*StreamSessionSummarySerializer.Meta.fields, "is_mature", "counts")
        read_only_fields = fields

    def get_counts(self, session: StreamSession) -> dict:
        return self.context.get("counts", {})


class DetectorConfigSerializer(serializers.Serializer):
    """The detector's live calibration, so scores can be interpreted.

    Read from the same `DetectorConfig` the detector runs on — the frontend
    never restates a threshold or weight of its own.
    """

    candidate_threshold = serializers.FloatField()
    auto_clip_threshold = serializers.FloatField()
    current_window_seconds = serializers.IntegerField()
    baseline_window_seconds = serializers.IntegerField()
    cooldown_seconds = serializers.IntegerField()
    minimum_current_messages = serializers.IntegerField()
    minimum_current_chatters = serializers.IntegerField()
    weights = serializers.DictField(child=serializers.FloatField())
    clip_freshness_seconds = serializers.FloatField()
    clip_verification_timeout_seconds = serializers.FloatField()

    @classmethod
    def current(cls) -> dict:
        return {
            "candidate_threshold": DETECTOR_CONFIG.candidate_threshold,
            # Reference only. Nothing acts on this: clip creation is manual.
            "auto_clip_threshold": DETECTOR_CONFIG.auto_clip_threshold,
            "current_window_seconds": DETECTOR_CONFIG.current_window_seconds,
            "baseline_window_seconds": DETECTOR_CONFIG.baseline_window_seconds,
            "cooldown_seconds": DETECTOR_CONFIG.moment_cooldown_seconds,
            "minimum_current_messages": DETECTOR_CONFIG.min_current_messages,
            "minimum_current_chatters": DETECTOR_CONFIG.min_current_unique_chatters,
            "weights": DETECTOR_CONFIG.weights(),
            "clip_freshness_seconds": CLIP_CONFIG.max_candidate_age_seconds,
            "clip_verification_timeout_seconds": CLIP_CONFIG.verification_timeout_seconds,
        }
