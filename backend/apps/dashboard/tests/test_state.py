"""The derived clip display state.

These are the distinctions the whole dashboard rests on, so they are tested
against the model layer directly rather than only through HTTP.
"""

from __future__ import annotations

import pytest

from apps.clips.models import Clip
from apps.dashboard import state
from apps.moments.models import MomentCandidateStatus

from .conftest import NOW, make_candidate

pytestmark = pytest.mark.django_db


def test_a_detected_moment_has_not_been_requested(live_session):
    candidate = make_candidate(live_session)

    assert state.derive_clip_state(candidate) == state.NOT_REQUESTED
    assert state.clip_for(candidate) is None


def test_a_rejected_moment_has_not_been_requested(live_session):
    candidate = make_candidate(live_session, status=MomentCandidateStatus.REJECTED)

    assert state.derive_clip_state(candidate) == state.NOT_REQUESTED


def test_a_claim_carrying_a_twitch_id_is_requested(live_session):
    candidate = make_candidate(live_session, status=MomentCandidateStatus.CLIP_REQUESTED)
    Clip.objects.create(moment=candidate, twitch_clip_id="SomeClipId", requested_at=NOW)

    assert state.derive_clip_state(candidate) == state.REQUESTED


def test_a_claim_without_a_twitch_id_is_unknown_not_failed(live_session):
    """The distinction the crash-consistency work exists to preserve.

    A claimed request with no id means nobody ever learned what Twitch did.
    Showing that as a failure would assert that no clip exists, which is
    precisely what is not known.
    """
    candidate = make_candidate(live_session, status=MomentCandidateStatus.CLIP_REQUESTED)
    Clip.objects.create(moment=candidate, requested_at=NOW)

    assert state.derive_clip_state(candidate) == state.REQUEST_UNKNOWN
    assert state.derive_clip_state(candidate) != state.FAILED


def test_a_claim_with_no_clip_row_at_all_is_still_only_requested(live_session):
    candidate = make_candidate(live_session, status=MomentCandidateStatus.CLIP_REQUESTED)

    assert state.derive_clip_state(candidate) == state.REQUESTED


def test_created_and_failed_follow_the_persisted_status(live_session):
    created = make_candidate(live_session, status=MomentCandidateStatus.CLIP_CREATED)
    failed = make_candidate(live_session, status=MomentCandidateStatus.FAILED)

    assert state.derive_clip_state(created) == state.CREATED
    assert state.derive_clip_state(failed) == state.FAILED


def test_deriving_state_never_writes(live_session, django_assert_max_num_queries):
    candidate = make_candidate(live_session, status=MomentCandidateStatus.CLIP_REQUESTED)
    Clip.objects.create(moment=candidate, requested_at=NOW)
    updated_at = candidate.updated_at

    # At most the one read of the reverse one-to-one, and never a write.
    with django_assert_max_num_queries(1):
        state.derive_clip_state(candidate)

    candidate.refresh_from_db()
    assert candidate.updated_at == updated_at
    assert candidate.status == MomentCandidateStatus.CLIP_REQUESTED
