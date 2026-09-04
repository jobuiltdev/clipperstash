"""Orchestration around the pure detector.

Reads the chat a single evaluation needs, hands it to the detector, applies the
cooldown and persists a candidate when one is warranted. The detector itself
never queries anything; everything that touches the database is here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from apps.moments.detector import DEFAULT_CONFIG, ChatSample, DetectorConfig, MomentScore
from apps.moments.detector.detector import score_samples
from apps.moments.detector.window import build_windows, require_aware
from apps.moments.models import MomentCandidate, MomentCandidateStatus
from apps.monitoring.models import ChatMessage, StreamSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvaluationResult:
    """What one evaluation decided, and what it did about it."""

    score: MomentScore
    candidate: MomentCandidate | None = None
    blocked_by_cooldown: bool = False
    persisted: bool = False


def load_samples(
    session: StreamSession,
    evaluation_time: datetime,
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> list[ChatSample]:
    """Read only the chat both windows can possibly contain.

    A bounded timestamp range over `(session, timestamp)` — the index added with
    chat ingestion — and only the four columns scoring reads. Chat history
    outside the ~70-second span is never touched, so the query cost does not
    grow with the length of the broadcast.
    """
    bounds = build_windows(evaluation_time, config=config)

    rows = ChatMessage.objects.filter(
        session=session,
        timestamp__gt=bounds.baseline_start,
        timestamp__lte=bounds.current_end,
    ).values_list("timestamp", "chatter_user_id_hash", "text", "emote_count")

    return [
        ChatSample(
            timestamp=timestamp,
            chatter_hash=chatter_hash,
            text=text,
            emote_count=emote_count,
        )
        for timestamp, chatter_hash, text, emote_count in rows
    ]


def is_in_cooldown(
    session: StreamSession,
    evaluation_time: datetime,
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> bool:
    """Whether a candidate was already recorded too recently.

    Cooldown is a persistence concern, not a scoring one, so the detector never
    learns about it. The boundary is inclusive of the cooldown length: a
    candidate exactly `moment_cooldown_seconds` earlier does **not** block, one
    a moment later does.
    """
    earliest_blocking = evaluation_time - timedelta(seconds=config.moment_cooldown_seconds)
    return MomentCandidate.objects.filter(
        session=session, detected_at__gt=earliest_blocking
    ).exists()


def evaluate_session(
    session: StreamSession,
    *,
    at: datetime | None = None,
    persist: bool = True,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> EvaluationResult:
    """Score one session's chat around `at`, recording a candidate if warranted.

    `at` defaults to now for a manual run, but any past instant may be supplied
    to replay a window deterministically. The session's status is not consulted
    and never changed: an ended broadcast can be re-scored from its stored chat,
    which is how calibration will work.
    """
    evaluation_time = at or timezone.now()
    require_aware(evaluation_time, "evaluation time")

    samples = load_samples(session, evaluation_time, config=config)
    score = score_samples(samples, evaluation_time, config=config)

    if not score.qualifies:
        return EvaluationResult(score=score)

    if is_in_cooldown(session, evaluation_time, config=config):
        logger.info(
            "Session %s scored %.2f but is within the moment cooldown.",
            session.pk,
            score.total,
        )
        return EvaluationResult(score=score, blocked_by_cooldown=True)

    if not persist:
        return EvaluationResult(score=score)

    candidate = _persist_candidate(session, score)
    logger.info(
        "Recorded moment candidate %s for session %s (score %.2f).",
        candidate.pk,
        session.pk,
        score.total,
    )
    return EvaluationResult(score=score, candidate=candidate, persisted=True)


def _persist_candidate(session: StreamSession, score: MomentScore) -> MomentCandidate:
    """Store the finding. Aggregates only — no text, ids or identities."""
    return MomentCandidate.objects.create(
        session=session,
        detected_at=score.evaluation_time,
        current_window_start=score.bounds.current_start,
        current_window_end=score.bounds.current_end,
        baseline_window_start=score.bounds.baseline_start,
        baseline_window_end=score.bounds.baseline_end,
        current_message_count=score.current.message_count,
        baseline_message_count=score.baseline.message_count,
        current_unique_chatters=score.current.unique_chatters,
        baseline_unique_chatters=score.baseline.unique_chatters,
        current_emote_count=score.current.emote_count,
        baseline_emote_count=score.baseline.emote_count,
        current_reaction_count=score.current.reaction_message_count,
        baseline_reaction_count=score.baseline.reaction_message_count,
        velocity_ratio=score.velocity_ratio,
        velocity_score=score.components.velocity,
        reaction_score=score.components.reaction,
        emote_score=score.components.emote,
        diversity_score=score.components.diversity,
        absolute_activity_score=score.components.absolute_activity,
        total_score=score.total,
        # The only status this milestone produces. Nothing requests or creates
        # a clip.
        status=MomentCandidateStatus.DETECTED,
    )
