"""Tests for `manage.py detect_moments`."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.moments.models import MomentCandidate

from .conftest import EVALUATION_TIME

pytestmark = pytest.mark.django_db

AT = EVALUATION_TIME.isoformat()


def run(*args, **kwargs) -> str:
    out = StringIO()
    call_command("detect_moments", *args, stdout=out, stderr=StringIO(), **kwargs)
    return out.getvalue()


def test_an_unknown_session_is_refused(db):
    with pytest.raises(CommandError, match="No stream session"):
        run(9999)


def test_a_quiet_session_reports_no_candidate(session, quiet_chat):
    quiet_chat(session)

    output = run(session.pk, at=AT)

    assert "Did not qualify" in output
    assert MomentCandidate.objects.count() == 0


def test_a_qualifying_session_records_and_reports_a_candidate(session, qualifying_chat):
    qualifying_chat(session)

    output = run(session.pk, at=AT)

    candidate = MomentCandidate.objects.get()
    assert "Qualified" in output
    assert f"moment candidate {candidate.pk}" in output


def test_the_output_reports_the_signals_and_scores(session, qualifying_chat):
    qualifying_chat(session)

    output = run(session.pk, at=AT)

    for expected in (
        "current window",
        "baseline window",
        "messages",
        "unique chatters",
        "emotes",
        "reaction messages",
        "velocity ratio",
        "velocity",
        "diversity",
        "absolute_activity",
        "Total score",
        "activity gate",
        "candidate threshold",
    ):
        assert expected in output, f"expected {expected!r} in the report"


def test_the_output_never_shows_chat_content_or_identities(session, qualifying_chat):
    qualifying_chat(session)

    output = run(session.pk, at=AT)

    assert "LMAO" not in output, "chat text is never printed"
    assert "hash-" not in output, "chatter hashes are never printed"
    assert "msg-" not in output


def test_no_persist_reports_without_recording(session, qualifying_chat):
    qualifying_chat(session)

    output = run(session.pk, at=AT, no_persist=True)

    assert "no-persist" in output
    assert MomentCandidate.objects.count() == 0


def test_the_cooldown_is_reported(session, qualifying_chat):
    qualifying_chat(session)
    run(session.pk, at=AT)

    later = EVALUATION_TIME + timedelta(seconds=5)
    qualifying_chat(session, at=later)
    output = run(session.pk, at=later.isoformat())

    assert "cooldown" in output.lower() or "already recorded" in output
    assert MomentCandidate.objects.count() == 1


def test_a_past_instant_replays_that_window(session, qualifying_chat):
    past = EVALUATION_TIME - timedelta(hours=2)
    qualifying_chat(session, at=past)

    output = run(session.pk, at=past.isoformat())

    assert "Qualified" in output
    assert MomentCandidate.objects.get().detected_at == past


def test_an_ended_session_can_be_replayed(ended_session, qualifying_chat):
    qualifying_chat(ended_session)

    output = run(ended_session.pk, at=AT)

    assert "Qualified" in output


@pytest.mark.parametrize("value", ["not-a-time", "18:00", "yesterday"])
def test_an_unreadable_at_value_is_refused(session, value):
    with pytest.raises(CommandError, match="ISO 8601"):
        run(session.pk, at=value)


def test_a_naive_at_value_is_refused(session):
    with pytest.raises(CommandError, match="timezone"):
        run(session.pk, at="2026-09-04T18:00:00")


def test_without_at_it_evaluates_now(session):
    output = run(session.pk)

    assert "Total score" in output
    assert MomentCandidate.objects.count() == 0


def test_the_command_creates_no_clip(session, qualifying_chat):
    """The detector records a finding; nothing requests or creates a clip."""
    qualifying_chat(session)

    output = run(session.pk, at=AT)

    assert "clip" not in output.lower().replace("no clip is created", "")
    assert MomentCandidate.objects.get().status == "detected"
