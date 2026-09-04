"""Tests for clip preconditions, claiming, verification and idempotency."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction

from apps.clips.config import DEFAULT_CONFIG
from apps.clips.exceptions import (
    CandidateNotEligibleError,
    ClipAuthorizationError,
    ClipRequestFailed,
    SessionNotLiveError,
    StaleCandidateError,
)
from apps.clips.models import Clip, ClipFailureCode
from apps.clips.services import claim_candidate, request_clip, verify_clip
from apps.moments.models import MomentCandidate, MomentCandidateStatus
from apps.monitoring.models import StreamSessionStatus
from apps.twitch.client import OAUTH_TOKEN_URL, TwitchClient
from apps.twitch.tests.conftest import json_response, routed_transport, token_payload

from .conftest import (
    CLIP_URL,
    EMPTY_CLIPS,
    HELIX_CLIPS_URL,
    NOW,
    TWITCH_CLIP_ID,
    accepted_payload,
    clip_payload,
    clip_transport,
    make_candidate,
)

pytestmark = pytest.mark.django_db


def run(candidate, transport, clock, **kwargs):
    return request_clip(
        candidate,
        now=kwargs.pop("now", NOW),
        client=TwitchClient(transport=transport),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        **kwargs,
    )


# ============================================================ preconditions ===


def test_a_fresh_detected_candidate_on_a_live_session_succeeds(candidate, connection, clock):
    result = run(candidate, clip_transport(), clock)

    assert result.created is True
    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.CLIP_CREATED


@pytest.mark.parametrize("age", [0, 1, 14.9, 15])
def test_a_candidate_within_the_freshness_budget_is_allowed(session, connection, clock, age):
    """Inclusive at the limit: exactly 15 seconds old still clips."""
    candidate = make_candidate(session, detected_at=NOW - timedelta(seconds=age))

    result = run(candidate, clip_transport(), clock)

    assert result.created is True


@pytest.mark.parametrize("age", [15.1, 30, 300, 86400])
def test_a_stale_candidate_is_refused(session, connection, clock, age):
    candidate = make_candidate(session, detected_at=NOW - timedelta(seconds=age))
    transport = clip_transport()

    with pytest.raises(StaleCandidateError) as exc_info:
        run(candidate, transport, clock)

    assert exc_info.value.code == "stale_moment_candidate"
    assert transport.call_count == 0, "a stale candidate never reaches Twitch"


def test_a_stale_candidate_is_not_marked_failed(session, connection, clock):
    """Staleness is a precondition, not a Twitch failure."""
    candidate = make_candidate(session, detected_at=NOW - timedelta(minutes=10))

    with pytest.raises(StaleCandidateError):
        run(candidate, clip_transport(), clock)

    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.DETECTED
    assert Clip.objects.count() == 0


def test_a_replayed_historical_candidate_is_refused(session, connection, clock):
    """A candidate from `detect_moments --at` is useful for calibration, not clipping."""
    candidate = make_candidate(session, detected_at=NOW - timedelta(days=2))
    transport = clip_transport()

    with pytest.raises(StaleCandidateError):
        run(candidate, transport, clock)

    assert transport.call_count == 0
    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.DETECTED


def test_a_slightly_future_candidate_is_tolerated_as_clock_skew(session, connection, clock):
    candidate = make_candidate(session, detected_at=NOW + timedelta(seconds=1))

    assert run(candidate, clip_transport(), clock).created is True


def test_a_materially_future_candidate_is_refused(session, connection, clock):
    candidate = make_candidate(session, detected_at=NOW + timedelta(minutes=5))
    transport = clip_transport()

    with pytest.raises(StaleCandidateError, match="future"):
        run(candidate, transport, clock)

    assert transport.call_count == 0


def test_an_ended_session_is_refused(ended_session, connection, clock):
    candidate = make_candidate(ended_session)
    transport = clip_transport()

    with pytest.raises(SessionNotLiveError) as exc_info:
        run(candidate, transport, clock)

    assert exc_info.value.code == "stream_not_live"
    assert transport.call_count == 0, "an ended session never reaches Twitch"


def test_an_ended_session_does_not_mark_the_candidate_failed(ended_session, connection, clock):
    candidate = make_candidate(ended_session)

    with pytest.raises(SessionNotLiveError):
        run(candidate, clip_transport(), clock)

    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.DETECTED
    assert Clip.objects.count() == 0


def test_the_current_session_state_is_read_not_remembered(candidate, connection, clock, session):
    """The in-memory object stays stale; the database is what decides."""
    session.status = StreamSessionStatus.ENDED
    type(session).objects.filter(pk=session.pk).update(status=StreamSessionStatus.ENDED)

    with pytest.raises(SessionNotLiveError):
        run(candidate, clip_transport(), clock)


def test_a_missing_connection_is_refused(candidate, clock):
    transport = clip_transport()

    with pytest.raises(ClipAuthorizationError, match="No Twitch account"):
        run(candidate, transport, clock)

    assert transport.call_count == 0


def test_a_connection_without_clips_edit_is_refused(candidate, connection, clock):
    connection.scopes = ["user:read:chat"]
    connection.save(update_fields=["scopes"])
    transport = clip_transport()

    with pytest.raises(ClipAuthorizationError) as exc_info:
        run(candidate, transport, clock)

    assert exc_info.value.code == "clip_not_authorized"
    assert transport.call_count == 0
    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.DETECTED


def test_a_connection_needing_reauthorization_is_refused(candidate, connection, clock):
    connection.mark_requires_reauthorization()

    with pytest.raises(ClipAuthorizationError):
        run(candidate, clip_transport(), clock)


@pytest.mark.parametrize(
    "status",
    [MomentCandidateStatus.REJECTED, MomentCandidateStatus.FAILED],
)
def test_an_ineligible_candidate_is_refused(session, connection, clock, status):
    candidate = make_candidate(session, status=status)
    transport = clip_transport()

    with pytest.raises(CandidateNotEligibleError):
        run(candidate, transport, clock)

    assert transport.call_count == 0


def test_a_failed_candidate_is_not_retried_automatically(session, connection, clock):
    candidate = make_candidate(session, status=MomentCandidateStatus.FAILED)

    with pytest.raises(CandidateNotEligibleError):
        run(candidate, clip_transport(), clock)

    assert Clip.objects.count() == 0


# =================================================================== claim ====


def test_claiming_moves_the_candidate_and_creates_one_clip(candidate):
    claimed, clip, newly = claim_candidate(candidate, now=NOW)

    assert newly is True
    assert claimed.status == MomentCandidateStatus.CLIP_REQUESTED
    assert clip.moment_id == candidate.pk
    assert clip.twitch_clip_id is None, "there is nothing to identify yet"
    assert clip.requested_at == NOW
    assert Clip.objects.count() == 1


def test_a_second_claim_returns_the_existing_clip(candidate):
    _, first, _ = claim_candidate(candidate, now=NOW)
    _, second, newly = claim_candidate(candidate, now=NOW)

    assert newly is False
    assert second.pk == first.pk
    assert Clip.objects.count() == 1


def test_a_duplicate_invocation_sends_no_second_request(candidate, connection, clock):
    run(candidate, clip_transport(), clock)

    transport = clip_transport(lookups=[json_response(200, clip_payload())])
    candidate.refresh_from_db()
    result = run(candidate, transport, clock)

    assert result.already_ready is True
    posts = [request for request in transport.requests if request.method == "POST"]
    assert posts == [], "an already-confirmed clip is never requested again"
    assert Clip.objects.count() == 1


def test_only_one_clip_row_can_exist_per_candidate(candidate):
    Clip.objects.create(moment=candidate, requested_at=NOW)

    with pytest.raises(IntegrityError), transaction.atomic():
        Clip.objects.create(moment=candidate, requested_at=NOW)


def test_a_twitch_clip_id_is_unique_once_populated(candidate, session):
    other = make_candidate(session, detected_at=NOW - timedelta(seconds=1))
    Clip.objects.create(moment=candidate, requested_at=NOW, twitch_clip_id="dup")

    with pytest.raises(IntegrityError), transaction.atomic():
        Clip.objects.create(moment=other, requested_at=NOW, twitch_clip_id="dup")


def test_unclaimed_clips_do_not_collide_on_a_null_clip_id(candidate, session):
    other = make_candidate(session, detected_at=NOW - timedelta(seconds=1))
    Clip.objects.create(moment=candidate, requested_at=NOW)
    Clip.objects.create(moment=other, requested_at=NOW)

    assert Clip.objects.filter(twitch_clip_id__isnull=True).count() == 2


def test_another_candidate_is_independent(candidate, session, connection, clock):
    other = make_candidate(session, detected_at=NOW - timedelta(seconds=2))

    run(candidate, clip_transport(), clock)
    run(
        other,
        clip_transport(
            create=json_response(202, accepted_payload("second-clip")),
            lookups=[json_response(200, clip_payload(clip_id="second-clip"))],
        ),
        clock,
    )

    assert Clip.objects.count() == 2
    assert set(Clip.objects.values_list("twitch_clip_id", flat=True)) == {
        TWITCH_CLIP_ID,
        "second-clip",
    }


# ============================================================ verification ====


def test_an_immediately_available_clip_is_confirmed(candidate, connection, clock):
    result = run(candidate, clip_transport(), clock)

    assert result.attempts == 1
    assert clock.slept == [], "no waiting was needed"

    clip = Clip.objects.get()
    assert clip.twitch_clip_id == TWITCH_CLIP_ID
    assert clip.twitch_url == CLIP_URL
    assert clip.title == "insane play"
    assert clip.duration == 28.5
    assert clip.thumbnail_url.endswith("-preview.jpg")
    assert clip.twitch_created_at is not None
    assert clip.ready_at is not None
    assert clip.is_ready is True


def test_polling_waits_between_attempts(candidate, connection, clock):
    transport = clip_transport(
        lookups=[
            json_response(200, EMPTY_CLIPS),
            json_response(200, EMPTY_CLIPS),
            json_response(200, clip_payload()),
        ]
    )

    result = run(candidate, transport, clock)

    assert result.attempts == 3
    assert clock.slept == [
        DEFAULT_CONFIG.verification_poll_seconds,
        DEFAULT_CONFIG.verification_poll_seconds,
    ]
    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.CLIP_CREATED


def test_an_empty_result_is_not_treated_as_failure(candidate, connection, clock):
    transport = clip_transport(
        lookups=[json_response(200, EMPTY_CLIPS), json_response(200, clip_payload())]
    )

    run(candidate, transport, clock)

    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.CLIP_CREATED


def test_the_deadline_ends_polling_and_fails_the_candidate(candidate, connection, clock):
    never = [json_response(200, EMPTY_CLIPS)] * 100
    transport = clip_transport(lookups=never)

    with pytest.raises(ClipRequestFailed) as exc_info:
        run(candidate, transport, clock)

    assert exc_info.value.code == ClipFailureCode.VERIFICATION_TIMEOUT
    assert sum(clock.slept) >= DEFAULT_CONFIG.verification_timeout_seconds

    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.FAILED


def test_a_timeout_preserves_the_twitch_clip_id_for_recovery(candidate, connection, clock):
    transport = clip_transport(lookups=[json_response(200, EMPTY_CLIPS)] * 100)

    with pytest.raises(ClipRequestFailed):
        run(candidate, transport, clock)

    clip = Clip.objects.get()
    assert clip.twitch_clip_id == TWITCH_CLIP_ID
    assert clip.failure_code == ClipFailureCode.VERIFICATION_TIMEOUT
    assert clip.ready_at is None
    assert clip.twitch_url == ""


def test_a_timeout_does_not_request_a_second_clip(candidate, connection, clock):
    transport = clip_transport(lookups=[json_response(200, EMPTY_CLIPS)] * 100)

    with pytest.raises(ClipRequestFailed):
        run(candidate, transport, clock)

    posts = [request for request in transport.requests if request.method == "POST"]
    assert len(posts) == 1


def test_the_configured_deadline_is_sixty_seconds():
    """Twitch's own documentation says 15s in one place and 60s in another.

    The larger bound is chosen deliberately; this pins the decision so changing
    it is a conscious act.
    """
    assert DEFAULT_CONFIG.verification_timeout_seconds == 60.0
    assert DEFAULT_CONFIG.verification_poll_seconds == 2.0
    assert DEFAULT_CONFIG.max_candidate_age_seconds == 15.0


def test_the_deadline_is_configurable(candidate, connection, clock):
    config = replace(DEFAULT_CONFIG, verification_timeout_seconds=4.0)
    transport = clip_transport(lookups=[json_response(200, EMPTY_CLIPS)] * 100)

    with pytest.raises(ClipRequestFailed):
        request_clip(
            candidate,
            now=NOW,
            client=TwitchClient(transport=transport),
            config=config,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    assert sum(clock.slept) <= 6.0


def test_a_broadcaster_mismatch_is_refused(candidate, connection, clock):
    transport = clip_transport(lookups=[json_response(200, clip_payload(broadcaster_id="999"))])

    with pytest.raises(ClipRequestFailed) as exc_info:
        run(candidate, transport, clock)

    assert exc_info.value.code == ClipFailureCode.BROADCASTER_MISMATCH
    clip = Clip.objects.get()
    assert clip.twitch_url == "", "another channel's clip is never attached"
    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.FAILED


def test_a_malformed_lookup_fails_verification(candidate, connection, clock):
    transport = clip_transport(lookups=[json_response(200, {"data": "nope"})])

    with pytest.raises(ClipRequestFailed) as exc_info:
        run(candidate, transport, clock)

    assert exc_info.value.code == ClipFailureCode.VERIFICATION_FAILED


# ================================================================ recovery ====


def test_an_interrupted_request_resumes_without_a_second_post(candidate, connection, clock):
    """The process stopped after Twitch accepted; verification picks it up."""
    _, clip, _ = claim_candidate(candidate, now=NOW)
    clip.twitch_clip_id = TWITCH_CLIP_ID
    clip.save(update_fields=["twitch_clip_id"])
    candidate.refresh_from_db()

    transport = clip_transport(lookups=[json_response(200, clip_payload())])
    result = run(candidate, transport, clock)

    posts = [request for request in transport.requests if request.method == "POST"]
    assert posts == [], "no second clip is requested"
    assert result.resumed is True
    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.CLIP_CREATED


def test_resuming_skips_the_freshness_check(session, connection, clock):
    """The moment was fresh when claimed; the accepted request is still valid."""
    candidate = make_candidate(session, detected_at=NOW - timedelta(minutes=30))
    _, clip, _ = claim_candidate(candidate, now=NOW)
    clip.twitch_clip_id = TWITCH_CLIP_ID
    clip.save(update_fields=["twitch_clip_id"])
    candidate.refresh_from_db()

    result = run(candidate, clip_transport(lookups=[json_response(200, clip_payload())]), clock)

    assert result.resumed is True


def test_verify_only_refuses_when_nothing_was_requested(candidate, connection, clock):
    transport = clip_transport()

    with pytest.raises(CandidateNotEligibleError, match="no clip request"):
        run(candidate, transport, clock, verify_only=True)

    assert transport.call_count == 0
    assert Clip.objects.count() == 0


def test_verify_only_confirms_an_accepted_request(candidate, connection, clock):
    _, clip, _ = claim_candidate(candidate, now=NOW)
    clip.twitch_clip_id = TWITCH_CLIP_ID
    clip.save(update_fields=["twitch_clip_id"])
    candidate.refresh_from_db()

    transport = clip_transport(lookups=[json_response(200, clip_payload())])
    result = run(candidate, transport, clock, verify_only=True)

    assert result.candidate.status == MomentCandidateStatus.CLIP_CREATED
    assert [r for r in transport.requests if r.method == "POST"] == []


def test_a_confirmed_clip_is_returned_idempotently(candidate, connection, clock):
    run(candidate, clip_transport(), clock)
    candidate.refresh_from_db()

    transport = clip_transport()
    result = run(candidate, transport, clock)

    assert result.already_ready is True
    assert transport.call_count == 0, "nothing is asked of Twitch"
    assert result.clip.twitch_url == CLIP_URL


def test_verify_clip_on_a_ready_clip_does_nothing(candidate, connection, clock):
    run(candidate, clip_transport(), clock)
    clip = Clip.objects.get()

    transport = clip_transport()
    result = verify_clip(
        clip,
        connection,
        client=TwitchClient(transport=transport),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert result.already_ready is True
    assert transport.call_count == 0


# =========================================================== create failures ==


@pytest.mark.parametrize("status", [400, 403, 404, 500, 503])
def test_a_rejected_request_fails_the_candidate(candidate, connection, clock, status):
    transport = clip_transport(create=json_response(status, {"message": "nope"}))

    with pytest.raises(ClipRequestFailed) as exc_info:
        run(candidate, transport, clock)

    assert exc_info.value.code == ClipFailureCode.TWITCH_CREATE_REJECTED
    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.FAILED
    assert Clip.objects.get().failure_code == ClipFailureCode.TWITCH_CREATE_REJECTED


def test_a_malformed_acceptance_fails_the_candidate(candidate, connection, clock):
    transport = clip_transport(create=json_response(202, {"data": []}))

    with pytest.raises(ClipRequestFailed) as exc_info:
        run(candidate, transport, clock)

    assert exc_info.value.code == ClipFailureCode.TWITCH_CREATE_MALFORMED
    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.FAILED


def test_an_unrecoverable_auth_failure_fails_the_candidate(candidate, connection, clock):
    transport = routed_transport(
        {
            HELIX_CLIPS_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(401, {"message": "Invalid OAuth token"}),
            ],
            OAUTH_TOKEN_URL: json_response(200, token_payload()),
        }
    )

    with pytest.raises(ClipRequestFailed) as exc_info:
        run(candidate, transport, clock)

    assert exc_info.value.code == ClipFailureCode.TWITCH_AUTH_FAILED
    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.FAILED
    connection.refresh_from_db()
    assert connection.requires_reauthorization is True


def test_a_clip_restriction_does_not_require_reauthorization(candidate, connection, clock):
    """403 means the channel disallows clips, not that the token is bad."""
    transport = clip_transport(create=json_response(403, {"message": "clips disabled"}))

    with pytest.raises(ClipRequestFailed):
        run(candidate, transport, clock)

    connection.refresh_from_db()
    assert connection.requires_reauthorization is False


def test_a_verification_timeout_does_not_require_reauthorization(candidate, connection, clock):
    transport = clip_transport(lookups=[json_response(200, EMPTY_CLIPS)] * 100)

    with pytest.raises(ClipRequestFailed):
        run(candidate, transport, clock)

    connection.refresh_from_db()
    assert connection.requires_reauthorization is False


# ================================================== stream session is never touched ==


@pytest.mark.parametrize(
    "transport_factory",
    [
        lambda: clip_transport(),
        lambda: clip_transport(create=json_response(404, {"message": "not live"})),
        lambda: clip_transport(lookups=[json_response(200, EMPTY_CLIPS)] * 100),
        lambda: clip_transport(lookups=[json_response(200, clip_payload(broadcaster_id="999"))]),
    ],
)
def test_clipping_never_changes_the_stream_session(
    candidate, connection, clock, session, transport_factory
):
    before = {
        "status": session.status,
        "ended_at": session.ended_at,
        "started_at": session.started_at,
        "updated_at": session.updated_at,
    }

    try:
        run(candidate, transport_factory(), clock)
    except ClipRequestFailed:
        pass

    session.refresh_from_db()
    assert session.status == before["status"] == StreamSessionStatus.LIVE
    assert session.ended_at == before["ended_at"] is None
    assert session.started_at == before["started_at"]
    assert session.updated_at == before["updated_at"]


def test_a_not_live_response_does_not_end_the_session(candidate, connection, clock, session):
    """Milestone 3 owns stream state; a 404 here proves nothing about it."""
    transport = clip_transport(create=json_response(404, {"message": "broadcaster not live"}))

    with pytest.raises(ClipRequestFailed):
        run(candidate, transport, clock)

    session.refresh_from_db()
    assert session.status == StreamSessionStatus.LIVE


# ================================================================== privacy ===


def test_the_clip_stores_no_secret_or_content(candidate, connection, clock):
    run(candidate, clip_transport(), clock)

    stored = Clip.objects.values().get()
    names = set(stored)
    for forbidden in ("access_token", "refresh_token", "client_secret", "raw_response", "edit_url"):
        assert forbidden not in names

    for name in names:
        assert "token" not in name
        assert "secret" not in name
        assert "chatter" not in name
        assert "hash" not in name

    assert "edit" not in str(stored), "the edit_url is never persisted"


def test_no_secret_appears_in_clip_failure_detail(candidate, connection, clock):
    transport = clip_transport(create=json_response(500, {"message": "Internal Server Error"}))

    with pytest.raises(ClipRequestFailed):
        run(candidate, transport, clock)

    clip = Clip.objects.get()
    assert "Internal Server Error" not in clip.failure_detail
    assert len(clip.failure_detail) <= 255


def test_no_secret_is_logged_during_a_run(candidate, connection, clock, caplog):
    import logging

    from apps.twitch.tests.conftest import ALL_FAKE_SECRETS
    from conftest import FAKE_CLIENT_SECRET

    with caplog.at_level(logging.DEBUG):
        run(candidate, clip_transport(), clock)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (*ALL_FAKE_SECRETS, FAKE_CLIENT_SECRET):
        assert secret not in logged
    assert "edit" not in logged.lower() or "edit_url" not in logged


def test_the_moment_candidate_is_protected_from_deletion(candidate, connection, clock):
    run(candidate, clip_transport(), clock)

    from django.db.models import ProtectedError

    with pytest.raises(ProtectedError), transaction.atomic():
        MomentCandidate.objects.filter(pk=candidate.pk).delete()


# ======================================================== late-binding guards ==


@pytest.mark.parametrize("parameter", ["monotonic", "sleep"])
def test_the_clock_and_sleeper_are_not_bound_at_import_time(parameter):
    """Guards the class of bug that once let a patched sleeper be ignored.

    A callable default here is evaluated when the module is imported, so
    replacing `services.time` afterwards would have no effect and a test could
    wait for a real minute.
    """
    import inspect

    from apps.clips import services

    for function in (services.verify_clip, services.request_clip):
        default = inspect.signature(function).parameters[parameter].default
        assert default is None, f"{function.__name__} binds {parameter} too early"


def test_replacing_the_time_module_is_honoured(candidate, connection):
    """The service must read its clock through the module, at call time."""
    from apps.clips import services

    class Recording:
        def __init__(self):
            self.value = 0.0
            self.slept = []

        def monotonic(self):
            return self.value

        def sleep(self, seconds):
            self.slept.append(seconds)
            self.value += seconds

    recorder = Recording()
    original = services.time
    services.time = recorder
    try:
        transport = clip_transport(
            lookups=[json_response(200, EMPTY_CLIPS), json_response(200, clip_payload())]
        )
        request_clip(candidate, now=NOW, client=TwitchClient(transport=transport))
    finally:
        services.time = original

    assert recorder.slept == [DEFAULT_CONFIG.verification_poll_seconds]


def test_the_transport_guard_refuses_a_client_without_a_mock(candidate, connection):
    """The suite-wide guard is what catches an unpatched client reference."""
    from apps.twitch.client import TwitchClient as RealClient

    with pytest.raises(AssertionError, match="never make a real Twitch HTTP request"):
        RealClient().get_clip("anything", access_token="t")
