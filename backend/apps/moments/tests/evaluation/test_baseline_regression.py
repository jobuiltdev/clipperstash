"""The baseline must be the detector Milestone 5 shipped, unchanged.

Milestone 8 makes configuration easy to vary. The risk that creates is that
introducing the machinery quietly moves the default — a rounded constant here,
a re-derived weight there — and every score in the database afterwards means
something slightly different from the ones before it.

These tests pin the production calibration to explicit literals and check that
the baseline path through the evaluation layer produces exactly what calling the
detector directly does.
"""

from __future__ import annotations

import pytest

from apps.moments.detector import DEFAULT_CONFIG
from apps.moments.detector.detector import score_samples
from apps.moments.evaluation import baseline
from apps.moments.evaluation.replay import replay_samples

# The Milestone 5 calibration, written out rather than read from the config, so a
# change to the default fails here instead of being confirmed by itself.
M5_CALIBRATION = {
    "current_window_seconds": 10,
    "baseline_window_seconds": 60,
    "min_current_messages": 5,
    "min_current_unique_chatters": 3,
    "candidate_threshold": 70.0,
    "auto_clip_threshold": 80.0,
    "moment_cooldown_seconds": 45,
    "velocity_weight": 0.40,
    "reaction_weight": 0.20,
    "diversity_weight": 0.15,
    "emote_weight": 0.15,
    "absolute_activity_weight": 0.10,
    "min_baseline_rate_per_second": 0.05,
    "velocity_saturation_ratio": 4.0,
    "velocity_confidence_messages": 10,
    "diversity_saturation_chatters": 15,
    "emote_saturation_per_message": 2.0,
    "reaction_saturation_chatters": 5,
    "absolute_activity_saturation_messages": 30,
}


@pytest.mark.parametrize(("field", "expected"), sorted(M5_CALIBRATION.items()))
def test_the_production_default_still_holds_its_milestone_5_value(field, expected):
    """Changing one of these is a production calibration change, not a refactor.

    It needs evidence from several sessions and an explicit decision, so it
    fails here first.
    """
    assert getattr(DEFAULT_CONFIG, field) == expected


def test_the_lexicon_is_unchanged():
    assert "poggers" in DEFAULT_CONFIG.reaction_tokens
    assert ("no", "way") in DEFAULT_CONFIG.reaction_phrases
    assert "lol" in DEFAULT_CONFIG.elongatable_tokens
    assert len(DEFAULT_CONFIG.reaction_tokens) == 20


def test_replaying_with_the_baseline_matches_calling_the_detector_directly(single_spike):
    """The evaluation layer adds a grid, not arithmetic.

    Every observation is compared against `score_samples` called with the same
    instant and the same samples. If replay ever grew scoring logic of its own,
    this would diverge.
    """
    result = replay_samples(single_spike, config=baseline().config, cadence_seconds=1.0)

    assert result.observations

    for observation in result.observations:
        direct = score_samples(single_spike, observation.evaluated_at, config=DEFAULT_CONFIG)

        assert observation.total_score == direct.total
        assert observation.velocity_score == direct.components.velocity
        assert observation.reaction_score == direct.components.reaction
        assert observation.diversity_score == direct.components.diversity
        assert observation.emote_score == direct.components.emote
        assert observation.absolute_activity_score == direct.components.absolute_activity
        assert observation.velocity_ratio == direct.velocity_ratio
        assert observation.gate_passed == direct.activity_gate_passed
        assert observation.qualifies == direct.qualifies
        assert observation.current_message_count == direct.current.message_count
        assert observation.baseline_message_count == direct.baseline.message_count


def test_the_baseline_and_an_explicitly_default_config_agree(single_spike):
    named = replay_samples(single_spike, config=baseline().config)
    explicit = replay_samples(single_spike, config=DEFAULT_CONFIG)

    assert named.observations == explicit.observations
