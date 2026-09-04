"""Tests for `manage.py create_clip`."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.clips.models import Clip
from apps.clips.services import claim_candidate
from apps.moments.models import MomentCandidateStatus
from apps.twitch.client import OAUTH_TOKEN_URL
from apps.twitch.tests.conftest import ALL_FAKE_SECRETS, json_response, routed_transport
from apps.twitch.tests.conftest import token_payload as twitch_token_payload
from conftest import FAKE_CLIENT_SECRET

from .conftest import (
    CLIP_URL,
    EMPTY_CLIPS,
    HELIX_CLIPS_URL,
    TWITCH_CLIP_ID,
    clip_payload,
    clip_transport,
    make_candidate,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def patch_clip_client(monkeypatch):
    """Route the clients the command builds through a mock transport."""

    def apply(transport) -> None:
        from apps.clips import services as clip_services
        from apps.twitch import services as twitch_services
        from apps.twitch.client import TwitchClient

        def build(*args, **kwargs):
            return TwitchClient(transport=transport)

        # Both modules import TwitchClient by name, so both references must be
        # replaced or one of them would build a client that talks to Twitch.
        monkeypatch.setattr(twitch_services, "TwitchClient", build)
        monkeypatch.setattr(clip_services, "TwitchClient", build)

    return apply


class _InstantTime:
    """Stands in for the `time` module: sleeping only advances a counter."""

    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture(autouse=True)
def instant_polling(monkeypatch):
    """Give the command a clock that only moves when it waits.

    The command resolves its clock and sleeper from the service module at call
    time, so replacing that reference makes a sixty-second verification deadline
    pass instantly. Autouse, so no test here can wait by accident.
    """
    from apps.clips import services

    monkeypatch.setattr(services, "time", _InstantTime())


@pytest.fixture
def fresh_candidate(db, session):
    """A candidate that is fresh against the real clock the command reads."""
    return make_candidate(session, detected_at=timezone.now())


def run(*args, **kwargs) -> str:
    out = StringIO()
    call_command("create_clip", *args, stdout=out, stderr=StringIO(), **kwargs)
    return out.getvalue()


def test_an_unknown_candidate_is_refused(db):
    with pytest.raises(CommandError, match="No moment candidate"):
        run(9999)


def test_a_fresh_candidate_is_clipped_and_reported(fresh_candidate, connection, patch_clip_client):
    patch_clip_client(clip_transport())

    output = run(fresh_candidate.pk)

    assert "Clip requested" in output
    assert TWITCH_CLIP_ID in output
    assert CLIP_URL in output
    assert "clip_created" in output

    fresh_candidate.refresh_from_db()
    assert fresh_candidate.status == MomentCandidateStatus.CLIP_CREATED


def test_the_output_reports_the_candidate_context(fresh_candidate, connection, patch_clip_client):
    patch_clip_client(clip_transport())

    output = run(fresh_candidate.pk)

    for expected in ("Moment", "session", "shroud", "score", "detected", "status", "freshness"):
        assert expected in output, f"expected {expected!r} in the report"


def test_a_stale_candidate_is_refused_with_its_code(session, connection, patch_clip_client):
    stale = make_candidate(session, detected_at=timezone.now() - timedelta(days=1))
    transport = clip_transport()
    patch_clip_client(transport)

    with pytest.raises(CommandError, match="stale_moment_candidate"):
        run(stale.pk)

    assert transport.call_count == 0
    stale.refresh_from_db()
    assert stale.status == MomentCandidateStatus.DETECTED


def test_an_ended_session_is_refused(ended_session, connection, patch_clip_client):
    candidate = make_candidate(ended_session, detected_at=timezone.now())
    transport = clip_transport()
    patch_clip_client(transport)

    with pytest.raises(CommandError, match="stream_not_live"):
        run(candidate.pk)

    assert transport.call_count == 0


def test_a_missing_connection_is_refused(fresh_candidate, patch_clip_client):
    patch_clip_client(clip_transport())

    with pytest.raises(CommandError, match="clip_not_authorized"):
        run(fresh_candidate.pk)


def test_a_connection_without_clips_edit_is_refused(fresh_candidate, connection, patch_clip_client):
    connection.scopes = ["user:read:chat"]
    connection.save(update_fields=["scopes"])
    patch_clip_client(clip_transport())

    with pytest.raises(CommandError, match="clip_not_authorized"):
        run(fresh_candidate.pk)


def test_a_rejected_request_reports_the_failure_code(
    fresh_candidate, connection, patch_clip_client
):
    patch_clip_client(clip_transport(create=json_response(403, {"message": "clips disabled"})))

    with pytest.raises(CommandError, match="twitch_create_rejected"):
        run(fresh_candidate.pk)

    fresh_candidate.refresh_from_db()
    assert fresh_candidate.status == MomentCandidateStatus.FAILED


def test_a_verification_timeout_reports_its_code(fresh_candidate, connection, patch_clip_client):
    patch_clip_client(clip_transport(lookups=[json_response(200, EMPTY_CLIPS)] * 100))

    with pytest.raises(CommandError, match="verification_timeout"):
        run(fresh_candidate.pk)

    fresh_candidate.refresh_from_db()
    assert fresh_candidate.status == MomentCandidateStatus.FAILED


def test_verify_only_resumes_an_accepted_request(fresh_candidate, connection, patch_clip_client):
    _, clip, _ = claim_candidate(fresh_candidate, now=timezone.now())
    clip.twitch_clip_id = TWITCH_CLIP_ID
    clip.save(update_fields=["twitch_clip_id"])

    transport = clip_transport(lookups=[json_response(200, clip_payload())])
    patch_clip_client(transport)

    output = run(fresh_candidate.pk, verify_only=True)

    assert "resumed" in output
    assert [r for r in transport.requests if r.method == "POST"] == []
    fresh_candidate.refresh_from_db()
    assert fresh_candidate.status == MomentCandidateStatus.CLIP_CREATED


def test_verify_only_refuses_when_nothing_was_requested(
    fresh_candidate, connection, patch_clip_client
):
    transport = clip_transport()
    patch_clip_client(transport)

    with pytest.raises(CommandError, match="candidate_not_eligible"):
        run(fresh_candidate.pk, verify_only=True)

    assert transport.call_count == 0


def test_a_confirmed_candidate_reports_idempotently(fresh_candidate, connection, patch_clip_client):
    patch_clip_client(clip_transport())
    run(fresh_candidate.pk)

    fresh_candidate.refresh_from_db()
    transport = clip_transport()
    patch_clip_client(transport)
    output = run(fresh_candidate.pk)

    assert "already confirmed" in output
    assert transport.call_count == 0
    assert Clip.objects.count() == 1


def test_the_output_never_exposes_a_secret_or_the_edit_url(
    fresh_candidate, connection, patch_clip_client
):
    patch_clip_client(clip_transport())

    output = run(fresh_candidate.pk)

    for secret in (*ALL_FAKE_SECRETS, FAKE_CLIENT_SECRET):
        assert secret not in output
    assert "edit_url" not in output
    assert "/edit" not in output


def test_the_output_never_exposes_a_raw_twitch_body(fresh_candidate, connection, patch_clip_client):
    patch_clip_client(
        clip_transport(create=json_response(500, {"message": "Internal Server Error"}))
    )

    with pytest.raises(CommandError) as exc_info:
        run(fresh_candidate.pk)

    assert "Internal Server Error" not in str(exc_info.value)


def test_an_auth_failure_reports_safely(fresh_candidate, connection, patch_clip_client):
    patch_clip_client(
        routed_transport(
            {
                HELIX_CLIPS_URL: [
                    json_response(401, {"message": "Invalid OAuth token"}),
                    json_response(401, {"message": "Invalid OAuth token"}),
                ],
                OAUTH_TOKEN_URL: json_response(200, twitch_token_payload()),
            }
        )
    )

    with pytest.raises(CommandError, match="twitch_auth_failed"):
        run(fresh_candidate.pk)
