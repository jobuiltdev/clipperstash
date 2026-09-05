"""A golden replay, pinned value by value.

The scenario tests describe behaviour and survive a deliberate recalibration.
This one does the opposite: it fixes every number a small replay produces, so
*any* change to scoring, windowing, cadence or cooldown fails loudly and has to
be acknowledged.

That matters most for the calibration tooling itself. If replay quietly drifted
away from the detector, every metric an operator collected afterwards would be
measuring something else, and nothing else in the suite would notice.

The dataset is 34 synthetic messages over 80 seconds, evaluated every 5 seconds:
small enough to write out in full, and containing a quiet stretch, the
incomplete-baseline warm-up, one clear burst and one cooldown suppression.
"""

from __future__ import annotations

import pytest

from apps.moments.evaluation.replay import replay_samples

from .conftest import at, chat, ordered

CADENCE_SECONDS = 5.0


@pytest.fixture
def golden_samples():
    """Fixed input. Fourteen calm messages, then a twenty-message reaction."""
    return ordered(
        chat(start=0.0, end=70.0, count=14, chatters=7),
        chat(
            start=70.0,
            end=80.0,
            count=20,
            chatters=16,
            text="LMAO no way clip it",
            emotes=2,
            chatter_prefix="hype",
        ),
    )


# offset, total, velocity, reaction, diversity, emote, activity, ratio,
# gate, crossed, creates, suppressed, baseline_complete, cur_msgs, base_msgs
GOLDEN = [
    (0, 9.66, 0.0333, 0.0, 0.5333, 0.0, 0.0333, 2.0, False, False, False, False, False, 1, 0),
    (5, 17.17, 0.2, 0.0, 0.5667, 0.0, 0.0667, 4.0, False, False, False, False, False, 2, 0),
    (10, 17.17, 0.2, 0.0, 0.5667, 0.0, 0.0667, 4.0, False, False, False, False, False, 2, 1),
    (15, 17.17, 0.2, 0.0, 0.5667, 0.0, 0.0667, 4.0, False, False, False, False, False, 2, 2),
    (20, 17.17, 0.2, 0.0, 0.5667, 0.0, 0.0667, 4.0, False, False, False, False, False, 2, 3),
    (25, 14.5, 0.1333, 0.0, 0.5667, 0.0, 0.0667, 3.0, False, False, False, False, False, 2, 4),
    (30, 12.9, 0.0933, 0.0, 0.5667, 0.0, 0.0667, 2.4, False, False, False, False, False, 2, 5),
    (35, 11.84, 0.0667, 0.0, 0.5667, 0.0, 0.0667, 2.0, False, False, False, False, False, 2, 6),
    (40, 11.07, 0.0476, 0.0, 0.5667, 0.0, 0.0667, 1.7143, False, False, False, False, False, 2, 7),
    (45, 10.5, 0.0333, 0.0, 0.5667, 0.0, 0.0667, 1.5, False, False, False, False, False, 2, 8),
    (50, 10.06, 0.0222, 0.0, 0.5667, 0.0, 0.0667, 1.3333, False, False, False, False, False, 2, 9),
    (55, 9.7, 0.0133, 0.0, 0.5667, 0.0, 0.0667, 1.2, False, False, False, False, False, 2, 10),
    (60, 9.41, 0.0061, 0.0, 0.5667, 0.0, 0.0667, 1.0909, False, False, False, False, False, 2, 11),
    (65, 9.17, 0.0, 0.0, 0.5667, 0.0, 0.0667, 1.0, False, False, False, False, False, 2, 12),
    (70, 18.67, 0.0, 0.1, 0.5667, 0.5, 0.0667, 1.0, False, False, False, False, True, 2, 12),
    (75, 91.67, 1.0, 1.0, 0.8667, 1.0, 0.3667, 5.5, True, True, True, False, True, 11, 12),
    (80, 95.15, 1.0, 1.0, 0.9211, 1.0, 0.6333, 9.5, True, True, False, True, True, 19, 12),
]


def test_the_grid_is_exactly_as_expected(golden_samples):
    result = replay_samples(golden_samples, cadence_seconds=CADENCE_SECONDS)

    assert [obs.evaluated_at for obs in result.observations] == [at(row[0]) for row in GOLDEN]


@pytest.mark.parametrize("row", GOLDEN, ids=lambda row: f"t+{row[0]}s")
def test_every_observation_matches_the_golden_values(golden_samples, row):
    (
        offset,
        total,
        velocity,
        reaction,
        diversity,
        emote,
        activity,
        ratio,
        gate,
        crossed,
        creates,
        suppressed,
        baseline_complete,
        current_messages,
        baseline_messages,
    ) = row

    result = replay_samples(golden_samples, cadence_seconds=CADENCE_SECONDS)
    observation = next(obs for obs in result.observations if obs.evaluated_at == at(offset))

    assert observation.total_score == total
    assert observation.velocity_score == velocity
    assert observation.reaction_score == reaction
    assert observation.diversity_score == diversity
    assert observation.emote_score == emote
    assert observation.absolute_activity_score == activity
    assert observation.velocity_ratio == ratio
    assert observation.gate_passed is gate
    assert observation.threshold_crossed is crossed
    assert observation.would_create_candidate is creates
    assert observation.cooldown_suppressed is suppressed
    assert observation.baseline_complete is baseline_complete
    assert observation.current_message_count == current_messages
    assert observation.baseline_message_count == baseline_messages


def test_exactly_one_candidate_at_a_fixed_instant(golden_samples):
    result = replay_samples(golden_samples, cadence_seconds=CADENCE_SECONDS)

    assert result.candidate_timestamps == (at(75),)


def test_the_second_crossing_is_suppressed_not_discarded(golden_samples):
    """It scored higher than the candidate; cooldown is the only reason it lost."""
    result = replay_samples(golden_samples, cadence_seconds=CADENCE_SECONDS)

    assert len(result.threshold_crossings) == 2
    assert len(result.candidates) == 1
    suppressed = result.suppressed[0]
    assert suppressed.total_score > result.candidates[0].total_score


def test_the_golden_replay_is_stable_across_runs(golden_samples):
    first = replay_samples(golden_samples, cadence_seconds=CADENCE_SECONDS)
    second = replay_samples(list(reversed(golden_samples)), cadence_seconds=CADENCE_SECONDS)

    assert first.observations == second.observations
