"""Tests for evaluation, cooldown and candidate persistence."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.moments.detector import DEFAULT_CONFIG
from apps.moments.models import MomentCandidate, MomentCandidateStatus
from apps.moments.services import evaluate_session, is_in_cooldown, load_samples
from apps.monitoring.models import ChatMessage, StreamSessionStatus

from .conftest import EVALUATION_TIME

pytestmark = pytest.mark.django_db


# ================================================================ scoring ====


def test_a_qualifying_evaluation_records_a_detected_candidate(session, qualifying_chat):
    qualifying_chat(session)

    result = evaluate_session(session, at=EVALUATION_TIME)

    assert result.score.qualifies is True
    assert result.persisted is True
    assert result.blocked_by_cooldown is False

    candidate = MomentCandidate.objects.get()
    assert candidate.session == session
    assert candidate.status == MomentCandidateStatus.DETECTED
    assert candidate.pk == result.candidate.pk


def test_a_quiet_evaluation_records_nothing(session, quiet_chat):
    quiet_chat(session)

    result = evaluate_session(session, at=EVALUATION_TIME)

    assert result.score.qualifies is False
    assert result.persisted is False
    assert result.candidate is None
    assert MomentCandidate.objects.count() == 0


def test_an_empty_session_records_nothing(session):
    result = evaluate_session(session, at=EVALUATION_TIME)

    assert result.score.current.message_count == 0
    assert result.score.qualifies is False
    assert MomentCandidate.objects.count() == 0


def test_no_persist_scores_without_recording(session, qualifying_chat):
    qualifying_chat(session)

    result = evaluate_session(session, at=EVALUATION_TIME, persist=False)

    assert result.score.qualifies is True
    assert result.persisted is False
    assert result.candidate is None
    assert MomentCandidate.objects.count() == 0


# ============================================================= persistence ===


def test_the_raw_metrics_are_persisted(session, qualifying_chat):
    qualifying_chat(session)

    candidate = evaluate_session(session, at=EVALUATION_TIME).candidate
    score = evaluate_session(session, at=EVALUATION_TIME, persist=False).score

    assert candidate.current_message_count == score.current.message_count == 30
    assert candidate.baseline_message_count == score.baseline.message_count == 30
    assert candidate.current_unique_chatters == score.current.unique_chatters == 25
    assert candidate.baseline_unique_chatters == score.baseline.unique_chatters == 12
    assert candidate.current_emote_count == score.current.emote_count == 60
    assert candidate.baseline_emote_count == score.baseline.emote_count == 0
    assert candidate.current_reaction_count == score.current.reaction_message_count == 30
    assert candidate.baseline_reaction_count == score.baseline.reaction_message_count == 0
    assert candidate.velocity_ratio == score.velocity_ratio


def test_the_component_scores_are_persisted(session, qualifying_chat):
    qualifying_chat(session)

    candidate = evaluate_session(session, at=EVALUATION_TIME).candidate
    components = evaluate_session(session, at=EVALUATION_TIME, persist=False).score.components

    assert candidate.velocity_score == components.velocity
    assert candidate.reaction_score == components.reaction
    assert candidate.emote_score == components.emote
    assert candidate.diversity_score == components.diversity
    assert candidate.absolute_activity_score == components.absolute_activity
    assert 0.0 <= candidate.total_score <= 100.0


def test_the_exact_window_boundaries_are_persisted(session, qualifying_chat):
    qualifying_chat(session)

    candidate = evaluate_session(session, at=EVALUATION_TIME).candidate

    assert candidate.detected_at == EVALUATION_TIME
    assert candidate.current_window_end == EVALUATION_TIME
    assert candidate.current_window_start == EVALUATION_TIME - timedelta(seconds=10)
    assert candidate.baseline_window_end == candidate.current_window_start
    assert candidate.baseline_window_start == EVALUATION_TIME - timedelta(seconds=70)


def test_no_chat_content_is_copied_into_the_candidate(session, qualifying_chat):
    qualifying_chat(session)

    evaluate_session(session, at=EVALUATION_TIME)

    stored = MomentCandidate.objects.values().get()
    serialized = str(stored)
    assert "LMAO" not in serialized, "message text is never copied"
    assert "hash-" not in serialized, "chatter identities are never copied"
    assert "msg-" not in serialized, "message ids are never copied"

    # Chatter columns are counts, never identities.
    assert "current_unique_chatters" in stored
    for name in stored:
        assert "text" not in name
        assert "hash" not in name
        assert "message_id" not in name
        assert name != "messages", "no message collection is stored"


def test_only_detected_is_ever_produced(session, qualifying_chat):
    qualifying_chat(session)

    evaluate_session(session, at=EVALUATION_TIME)

    statuses = set(MomentCandidate.objects.values_list("status", flat=True))
    assert statuses == {MomentCandidateStatus.DETECTED}
    assert MomentCandidateStatus.CLIP_REQUESTED not in statuses
    assert MomentCandidateStatus.CLIP_CREATED not in statuses


# ================================================================ cooldown ===


def test_a_second_evaluation_inside_the_cooldown_is_blocked(session, qualifying_chat):
    qualifying_chat(session)
    evaluate_session(session, at=EVALUATION_TIME)

    later = EVALUATION_TIME + timedelta(seconds=5)
    qualifying_chat(session, at=later)
    result = evaluate_session(session, at=later)

    assert result.score.qualifies is True
    assert result.blocked_by_cooldown is True
    assert result.persisted is False
    assert MomentCandidate.objects.count() == 1


def test_a_candidate_just_inside_the_cooldown_still_blocks(session, qualifying_chat):
    qualifying_chat(session)
    evaluate_session(session, at=EVALUATION_TIME)

    later = EVALUATION_TIME + timedelta(seconds=44.999)
    qualifying_chat(session, at=later)

    assert is_in_cooldown(session, later) is True
    assert evaluate_session(session, at=later).blocked_by_cooldown is True
    assert MomentCandidate.objects.count() == 1


def test_exactly_the_cooldown_length_later_is_allowed(session, qualifying_chat):
    """The boundary is inclusive: 45s after a candidate, another may be recorded."""
    qualifying_chat(session)
    evaluate_session(session, at=EVALUATION_TIME)

    later = EVALUATION_TIME + timedelta(seconds=DEFAULT_CONFIG.moment_cooldown_seconds)
    qualifying_chat(session, at=later)

    assert is_in_cooldown(session, later) is False
    result = evaluate_session(session, at=later)

    assert result.persisted is True
    assert MomentCandidate.objects.count() == 2


def test_well_after_the_cooldown_a_new_candidate_is_recorded(session, qualifying_chat):
    qualifying_chat(session)
    evaluate_session(session, at=EVALUATION_TIME)

    later = EVALUATION_TIME + timedelta(seconds=120)
    qualifying_chat(session, at=later)

    assert evaluate_session(session, at=later).persisted is True
    assert MomentCandidate.objects.count() == 2


def test_the_cooldown_is_per_session(session, other_session, qualifying_chat):
    qualifying_chat(session)
    qualifying_chat(other_session)

    evaluate_session(session, at=EVALUATION_TIME)
    result = evaluate_session(other_session, at=EVALUATION_TIME)

    assert result.persisted is True
    assert MomentCandidate.objects.filter(session=session).count() == 1
    assert MomentCandidate.objects.filter(session=other_session).count() == 1


def test_the_cooldown_length_is_configurable(session, qualifying_chat):
    config = replace(DEFAULT_CONFIG, moment_cooldown_seconds=5)
    qualifying_chat(session)
    evaluate_session(session, at=EVALUATION_TIME, config=config)

    later = EVALUATION_TIME + timedelta(seconds=6)
    qualifying_chat(session, at=later)

    assert evaluate_session(session, at=later, config=config).persisted is True


# ================================================================ isolation ==


def test_another_sessions_chat_is_not_read(session, other_session, qualifying_chat):
    qualifying_chat(other_session)

    result = evaluate_session(session, at=EVALUATION_TIME)

    assert result.score.current.message_count == 0
    assert MomentCandidate.objects.count() == 0


def test_evaluation_does_not_mutate_the_stream_session(session, qualifying_chat):
    qualifying_chat(session)
    before = {
        "status": session.status,
        "ended_at": session.ended_at,
        "started_at": session.started_at,
        "updated_at": session.updated_at,
    }

    evaluate_session(session, at=EVALUATION_TIME)

    session.refresh_from_db()
    assert session.status == before["status"] == StreamSessionStatus.LIVE
    assert session.ended_at == before["ended_at"]
    assert session.started_at == before["started_at"]
    assert session.updated_at == before["updated_at"]


def test_evaluation_does_not_mutate_chat_messages(session, qualifying_chat):
    qualifying_chat(session)
    before = list(ChatMessage.objects.order_by("pk").values())

    evaluate_session(session, at=EVALUATION_TIME)

    assert list(ChatMessage.objects.order_by("pk").values()) == before


# ============================================================== replay =======


def test_an_ended_session_can_be_replayed(ended_session, qualifying_chat):
    """Historical scoring must work after the broadcast is over."""
    qualifying_chat(ended_session)

    result = evaluate_session(ended_session, at=EVALUATION_TIME)

    assert result.persisted is True
    ended_session.refresh_from_db()
    assert ended_session.status == StreamSessionStatus.ENDED


def test_replaying_a_past_window_is_deterministic(session, qualifying_chat):
    past = EVALUATION_TIME - timedelta(hours=2)
    qualifying_chat(session, at=past)

    first = evaluate_session(session, at=past, persist=False).score
    second = evaluate_session(session, at=past, persist=False).score

    assert first.total == second.total
    assert first.components == second.components
    assert first.evaluation_time == past


def test_a_replayed_candidate_records_the_replayed_instant(session, qualifying_chat):
    past = EVALUATION_TIME - timedelta(hours=2)
    qualifying_chat(session, at=past)

    candidate = evaluate_session(session, at=past).candidate

    assert candidate.detected_at == past
    assert candidate.current_window_end == past


def test_a_window_with_no_chat_around_it_scores_nothing(session, qualifying_chat):
    qualifying_chat(session)

    result = evaluate_session(session, at=EVALUATION_TIME - timedelta(days=1), persist=False)

    assert result.score.current.message_count == 0
    assert result.score.baseline.message_count == 0
    assert result.score.qualifies is False


# ============================================================== querying =====


def test_only_the_two_windows_are_read(session, add_messages, qualifying_chat):
    """Chat outside the ~70-second span is never loaded."""
    qualifying_chat(session)
    add_messages(session, 50, unique=20, seconds_before_start=3600, seconds_before_end=3000)

    samples = load_samples(session, EVALUATION_TIME)

    assert len(samples) == 60, "only the baseline and current windows"
    assert all(sample.timestamp > EVALUATION_TIME - timedelta(seconds=70) for sample in samples)


def test_boundary_messages_are_loaded_but_split_correctly(session, add_messages):
    add_messages(session, 1, unique=1, seconds_before_start=10, seconds_before_end=10)
    add_messages(session, 1, unique=1, seconds_before_start=70, seconds_before_end=70)

    samples = load_samples(session, EVALUATION_TIME)
    score = evaluate_session(session, at=EVALUATION_TIME, persist=False).score

    assert len(samples) == 1, "the far baseline edge is outside the query too"
    assert score.baseline.message_count == 1
    assert score.current.message_count == 0


def test_an_evaluation_runs_a_small_bounded_number_of_queries(session, qualifying_chat):
    qualifying_chat(session)

    with CaptureQueriesContext(connection) as captured:
        evaluate_session(session, at=EVALUATION_TIME)

    # One chat read, one cooldown check, one insert.
    assert len(captured) <= 4, [query["sql"][:80] for query in captured]


def test_the_chat_read_is_bounded_by_timestamp(session, qualifying_chat):
    qualifying_chat(session)

    with CaptureQueriesContext(connection) as captured:
        load_samples(session, EVALUATION_TIME)

    assert len(captured) == 1
    sql = captured[0]["sql"]
    assert "timestamp" in sql
    assert "session_id" in sql


# ============================================================== validation ===


def test_a_naive_evaluation_time_is_refused(session):
    from apps.moments.detector import DetectorInputError

    with pytest.raises(DetectorInputError):
        evaluate_session(session, at=EVALUATION_TIME.replace(tzinfo=None))


def test_the_default_evaluation_time_is_now(session):
    """A manual run with no `--at` still works, using the current instant."""
    result = evaluate_session(session, persist=False)

    assert result.score.evaluation_time is not None
    assert result.score.evaluation_time.tzinfo is not None
