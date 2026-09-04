"""What happens when a clip request's outcome cannot be known.

The claim protects two concurrent callers from both asking Twitch. It cannot
protect against the process stopping between the claim committing and the clip
id reaching the database — Twitch offers no caller-supplied idempotency key, so
there is no way to ask whether the first request landed. These tests pin the
behavior that follows from that: never guess, never re-send.
"""

from __future__ import annotations

import httpx
import pytest

from apps.clips.exceptions import ClipRequestFailed, ClipRequestStateUnknown
from apps.clips.models import Clip, ClipFailureCode
from apps.clips.services import claim_candidate, request_clip, verify_clip
from apps.moments.models import MomentCandidateStatus
from apps.monitoring.models import StreamSessionStatus
from apps.twitch.client import OAUTH_TOKEN_URL, TwitchClient
from apps.twitch.tests.conftest import (
    FAKE_ACCESS_TOKEN,
    FAKE_ACCESS_TOKEN_2,
    json_response,
    routed_transport,
    token_payload,
)

from .conftest import (
    EMPTY_CLIPS,
    HELIX_CLIPS_URL,
    NOW,
    TWITCH_CLIP_ID,
    accepted_payload,
    clip_payload,
    clip_transport,
)

pytestmark = pytest.mark.django_db


def run(candidate, transport, clock, **kwargs):
    return request_clip(
        candidate,
        now=NOW,
        client=TwitchClient(transport=transport),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        **kwargs,
    )


def posts(transport):
    """Create Clip requests only — the token endpoint is also a POST."""
    return [
        request
        for request in transport.requests
        if request.method == "POST" and str(request.url).split("?")[0] == HELIX_CLIPS_URL
    ]


def gets(transport):
    return [
        request
        for request in transport.requests
        if request.method == "GET" and str(request.url).split("?")[0] == HELIX_CLIPS_URL
    ]


def timing_out_transport():
    """A transport whose clip POST never completes."""

    def explode(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("the response never arrived")

    return clip_transport(create=explode)


# ================================================ 1. interrupted before the id ==


def test_an_interruption_before_the_id_leaves_a_claimed_but_unidentified_clip(
    candidate, connection, clock, session, monkeypatch
):
    """The crash window: claimed, committed, and then the process stops."""
    from apps.clips import services

    def die(*args, **kwargs):
        raise KeyboardInterrupt("the process stopped mid-request")

    monkeypatch.setattr(services, "_send_request", die)

    with pytest.raises(KeyboardInterrupt):
        run(candidate, clip_transport(), clock)

    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.CLIP_REQUESTED

    assert Clip.objects.count() == 1
    clip = Clip.objects.get()
    assert clip.moment_id == candidate.pk
    assert clip.twitch_clip_id is None

    session.refresh_from_db()
    assert session.status == StreamSessionStatus.LIVE
    assert session.ended_at is None


# ============================================ 2. a later run refuses to re-send ==


def test_a_second_invocation_refuses_to_send_another_request(candidate, connection, clock):
    claim_candidate(candidate, now=NOW)
    candidate.refresh_from_db()
    transport = clip_transport()

    with pytest.raises(ClipRequestStateUnknown) as exc_info:
        run(candidate, transport, clock)

    assert exc_info.value.code == "clip_request_state_unknown"
    assert posts(transport) == [], "a duplicate clip must never be requested"
    assert transport.call_count == 0, "Twitch is not contacted at all"

    assert Clip.objects.count() == 1
    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.CLIP_REQUESTED
    assert Clip.objects.get().twitch_clip_id is None


def test_the_unknown_state_is_never_reverted_or_failed(candidate, connection, clock):
    claim_candidate(candidate, now=NOW)
    candidate.refresh_from_db()

    for _ in range(3):
        with pytest.raises(ClipRequestStateUnknown):
            run(candidate, clip_transport(), clock)

    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.CLIP_REQUESTED, (
        "never reset to DETECTED, never marked FAILED"
    )
    assert Clip.objects.count() == 1


def test_the_unknown_outcome_is_recorded_without_failing_the_candidate(
    candidate, connection, clock
):
    """Recorded so the state is visible, but the candidate is not FAILED."""
    with pytest.raises(ClipRequestStateUnknown):
        run(candidate, timing_out_transport(), clock)

    clip = Clip.objects.get()
    assert clip.failure_code == ClipFailureCode.REQUEST_STATE_UNKNOWN
    assert clip.twitch_clip_id is None
    assert clip.ready_at is None

    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.CLIP_REQUESTED


# ================================================= 3. verify-only with no id ====


def test_verify_only_without_an_id_makes_no_twitch_call(candidate, connection, clock):
    claim_candidate(candidate, now=NOW)
    candidate.refresh_from_db()
    transport = clip_transport()

    with pytest.raises(ClipRequestStateUnknown) as exc_info:
        run(candidate, transport, clock, verify_only=True)

    assert posts(transport) == []
    assert gets(transport) == [], "Get Clips is never called without an id"
    assert transport.call_count == 0
    assert "cannot be verified" in str(exc_info.value)


def test_verify_clip_directly_refuses_a_clip_with_no_id(candidate, connection, clock):
    _, clip, _ = claim_candidate(candidate, now=NOW)
    transport = clip_transport()

    with pytest.raises(ClipRequestStateUnknown):
        verify_clip(
            clip,
            connection,
            client=TwitchClient(transport=transport),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    assert transport.call_count == 0
    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.CLIP_REQUESTED


# ========================================== 4. ambiguous timeout during POST ====


def test_an_ambiguous_timeout_is_not_treated_as_a_failure(candidate, connection, clock, session):
    """A timeout cannot tell "never arrived" from "arrived and was accepted"."""
    transport = timing_out_transport()

    with pytest.raises(ClipRequestStateUnknown):
        run(candidate, transport, clock)

    assert len(posts(transport)) == 1, "exactly one request was attempted"

    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.CLIP_REQUESTED
    assert Clip.objects.get().twitch_clip_id is None

    connection.refresh_from_db()
    assert connection.requires_reauthorization is False, "a timeout is not an auth problem"

    session.refresh_from_db()
    assert session.status == StreamSessionStatus.LIVE
    assert session.ended_at is None


@pytest.mark.parametrize(
    "error",
    [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError],
)
def test_every_ambiguous_transport_failure_yields_the_unknown_state(
    candidate, connection, clock, error
):
    def explode(_request: httpx.Request) -> httpx.Response:
        raise error("uncertain")

    with pytest.raises(ClipRequestStateUnknown):
        run(candidate, clip_transport(create=explode), clock)

    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.CLIP_REQUESTED


# ========================================== 5. the one permitted automatic retry ==


def test_a_definitive_401_still_refreshes_once_and_retries_once(candidate, connection, clock):
    """The only automatic Create Clip retry: Twitch explicitly rejected the first."""
    transport = routed_transport(
        {
            HELIX_CLIPS_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(202, accepted_payload()),
                json_response(200, clip_payload()),
            ],
            OAUTH_TOKEN_URL: json_response(
                200, token_payload(access_token=FAKE_ACCESS_TOKEN_2, refresh_token=None)
            ),
        }
    )

    run(candidate, transport, clock)

    assert len(posts(transport)) == 2, "the rejected request and its one retry"
    assert posts(transport)[0].headers["Authorization"] == f"Bearer {FAKE_ACCESS_TOKEN}"
    assert posts(transport)[1].headers["Authorization"] == f"Bearer {FAKE_ACCESS_TOKEN_2}"

    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.CLIP_CREATED


# ================================================== 6. a second 401 is definite ==


def test_a_second_401_is_a_known_auth_failure(candidate, connection, clock):
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
    assert len(posts(transport)) == 2, "no third attempt"
    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.FAILED


# ============================================ 7. definitive refusals send once ==


@pytest.mark.parametrize("status", [400, 403, 404])
def test_a_definitive_refusal_is_sent_exactly_once(candidate, connection, clock, status):
    transport = clip_transport(create=json_response(status, {"message": "no"}))

    with pytest.raises(ClipRequestFailed) as exc_info:
        run(candidate, transport, clock)

    assert exc_info.value.code == ClipFailureCode.TWITCH_CREATE_REJECTED
    assert len(posts(transport)) == 1, "a definitive refusal is never re-sent"
    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.FAILED


@pytest.mark.parametrize("status", [500, 503])
def test_a_server_error_is_also_sent_exactly_once(candidate, connection, clock, status):
    """Twitch answered, so the outcome is known even though it is a 5xx."""
    transport = clip_transport(create=json_response(status, {"message": "boom"}))

    with pytest.raises(ClipRequestFailed):
        run(candidate, transport, clock)

    assert len(posts(transport)) == 1


# ============================================= 8/9. the safe recovery paths ====


def test_a_claim_with_an_id_verifies_and_never_posts(candidate, connection, clock):
    _, clip, _ = claim_candidate(candidate, now=NOW)
    clip.twitch_clip_id = TWITCH_CLIP_ID
    clip.save(update_fields=["twitch_clip_id"])
    candidate.refresh_from_db()

    transport = clip_transport(
        lookups=[json_response(200, EMPTY_CLIPS), json_response(200, clip_payload())]
    )
    result = run(candidate, transport, clock)

    assert posts(transport) == [], "recovery never sends another request"
    assert len(gets(transport)) == 2
    assert result.resumed is True
    candidate.refresh_from_db()
    assert candidate.status == MomentCandidateStatus.CLIP_CREATED


def test_a_confirmed_clip_makes_no_twitch_call(candidate, connection, clock):
    run(candidate, clip_transport(), clock)
    candidate.refresh_from_db()

    transport = clip_transport()
    result = run(candidate, transport, clock)

    assert transport.call_count == 0
    assert result.already_ready is True


# =========================================== 10. the claim itself is unchanged ==


def test_the_claim_still_precedes_any_request(candidate, connection, clock):
    """Ordering the correction must not disturb: claim first, then Twitch."""
    order: list[str] = []

    from apps.clips import services

    original_claim = services.claim_candidate

    def watched_claim(*args, **kwargs):
        order.append("claim")
        return original_claim(*args, **kwargs)

    def watching_transport():
        def on_post(_request):
            order.append("post")
            return json_response(202, accepted_payload())

        return clip_transport(create=on_post)

    services.claim_candidate = watched_claim
    try:
        run(candidate, watching_transport(), clock)
    finally:
        services.claim_candidate = original_claim

    assert order == ["claim", "post"]


def test_a_second_claim_still_returns_the_existing_row(candidate):
    _, first, newly_first = claim_candidate(candidate, now=NOW)
    _, second, newly_second = claim_candidate(candidate, now=NOW)

    assert newly_first is True
    assert newly_second is False
    assert first.pk == second.pk
    assert Clip.objects.count() == 1


def test_no_transaction_is_held_across_the_twitch_call(candidate, connection, clock):
    """The claim commits before any HTTP, so nothing waits on a held lock."""
    from django.db import transaction as db_transaction

    observed: list[bool] = []

    def on_post(_request):
        observed.append(db_transaction.get_connection().in_atomic_block)
        return json_response(202, accepted_payload())

    run(candidate, clip_transport(create=on_post), clock)

    # pytest-django wraps each test in a transaction, so the meaningful check is
    # that the claim's own atomic block has already exited by request time.
    assert observed, "the request was made"
    assert db_transaction.get_connection().savepoint_ids == [], "no claim savepoint is still open"
