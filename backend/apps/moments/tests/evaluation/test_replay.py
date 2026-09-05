"""The replay engine: its grid, its determinism and its cooldown simulation."""

from __future__ import annotations

from datetime import timedelta

import pytest

from apps.moments.detector import DEFAULT_CONFIG
from apps.moments.evaluation.errors import ReplayError
from apps.moments.evaluation.replay import (
    GATE_TOO_FEW_MESSAGES,
    GATE_TOO_FEW_MESSAGES_AND_CHATTERS,
    MAX_TICKS,
    build_ticks,
    replay_samples,
)

from .conftest import at, calm_baseline, chat, hype_burst, ordered

# -- the grid -----------------------------------------------------------------


def test_ticks_are_aligned_to_whole_seconds():
    """A re-collection at a slightly different sub-second offset must replay the same."""
    samples = chat(start=0.4, end=10.4, count=10, chatters=5)

    ticks = build_ticks(samples, cadence_seconds=1.0)

    assert all(tick.microsecond == 0 for tick in ticks)
    assert ticks[0] == at(0)


def test_the_grid_steps_by_the_cadence():
    samples = chat(start=0.0, end=10.0, count=10, chatters=5)

    ticks = build_ticks(samples, cadence_seconds=2.5)

    assert [(tick - ticks[0]).total_seconds() for tick in ticks] == [0.0, 2.5, 5.0, 7.5, 10.0]


def test_the_grid_reaches_past_the_last_message():
    """Otherwise the final messages would sit beyond every current window."""
    samples = chat(start=0.0, end=10.0, count=10, chatters=5)

    ticks = build_ticks(samples, cadence_seconds=1.0)

    assert ticks[-1] >= samples[-1].timestamp


def test_an_empty_session_has_no_grid():
    assert build_ticks([], cadence_seconds=1.0) == []


def test_a_zero_cadence_is_refused():
    with pytest.raises(ReplayError, match="greater than zero"):
        build_ticks(chat(start=0.0, end=1.0, count=1, chatters=1), cadence_seconds=0)


def test_a_negative_cadence_is_refused():
    with pytest.raises(ReplayError, match="greater than zero"):
        build_ticks(chat(start=0.0, end=1.0, count=1, chatters=1), cadence_seconds=-1)


def test_an_absurdly_fine_cadence_over_a_long_session_is_refused():
    """A grid nobody could read, and an evaluation nobody would wait for."""
    samples = chat(start=0.0, end=100_000.0, count=10, chatters=3)

    with pytest.raises(ReplayError, match=str(MAX_TICKS)):
        build_ticks(samples, cadence_seconds=0.001)


# -- determinism --------------------------------------------------------------


def test_the_same_input_always_replays_identically(single_spike):
    first = replay_samples(single_spike, cadence_seconds=1.0)
    second = replay_samples(single_spike, cadence_seconds=1.0)

    assert first.observations == second.observations


def test_input_order_does_not_change_the_result(single_spike):
    """Replay sorts its input, so a differently ordered query cannot shift a score."""
    shuffled = list(reversed(single_spike))

    assert replay_samples(shuffled).observations == replay_samples(single_spike).observations


def test_the_cadence_is_reported_with_the_result(single_spike):
    result = replay_samples(single_spike, cadence_seconds=2.0)

    assert result.cadence_seconds == 2.0
    assert result.sample_count == len(single_spike)


def test_a_coarser_cadence_produces_fewer_observations(single_spike):
    fine = replay_samples(single_spike, cadence_seconds=1.0)
    coarse = replay_samples(single_spike, cadence_seconds=5.0)

    assert len(coarse.observations) < len(fine.observations)


def test_an_empty_session_replays_to_nothing():
    result = replay_samples([])

    assert result.observations == ()
    assert result.first_tick is None
    assert result.duration_seconds == 0.0


# -- the quiet case -----------------------------------------------------------


def test_a_quiet_stream_produces_no_candidate(quiet_stream):
    result = replay_samples(quiet_stream)

    assert result.observations
    assert result.candidates == ()
    assert result.threshold_crossings == ()


def test_a_quiet_window_says_why_the_gate_refused_it(quiet_stream):
    result = replay_samples(quiet_stream)
    refused = [obs for obs in result.observations if not obs.gate_passed]

    assert refused
    assert all(
        obs.gate_reason in {GATE_TOO_FEW_MESSAGES, GATE_TOO_FEW_MESSAGES_AND_CHATTERS}
        for obs in refused
    )


def test_a_passing_window_reports_no_gate_reason(single_spike):
    result = replay_samples(single_spike)
    passing = [obs for obs in result.observations if obs.gate_passed]

    assert passing
    assert all(obs.gate_reason is None for obs in passing)


# -- threshold crossing versus candidate creation -----------------------------


def test_a_spike_crosses_the_threshold_many_times_but_creates_one_candidate(single_spike):
    """The distinction the whole calibration workflow rests on.

    One burst is one moment. Production creates a single candidate for it, but
    the detector saw it on every tick — and throwing those away would hide how
    strongly it was seen.
    """
    result = replay_samples(single_spike)

    assert len(result.threshold_crossings) > 1
    assert len(result.candidates) == 1
    assert len(result.suppressed) == len(result.threshold_crossings) - 1


def test_a_suppressed_observation_still_reports_its_score(single_spike):
    result = replay_samples(single_spike)
    suppressed = result.suppressed

    assert suppressed
    for observation in suppressed:
        assert observation.threshold_crossed
        assert observation.gate_passed
        assert observation.total_score >= DEFAULT_CONFIG.candidate_threshold
        assert observation.would_create_candidate is False


def test_a_candidate_observation_is_not_marked_suppressed(single_spike):
    result = replay_samples(single_spike)

    for observation in result.candidates:
        assert observation.cooldown_suppressed is False
        assert observation.qualifies


def test_the_second_distinct_moment_becomes_eligible_again(two_distinct_moments):
    """After the cooldown expires, a genuinely separate event is a new candidate."""
    result = replay_samples(two_distinct_moments)

    assert len(result.candidates) == 2

    first, second = result.candidate_timestamps
    gap = (second - first).total_seconds()
    assert gap > DEFAULT_CONFIG.moment_cooldown_seconds


def test_a_longer_cooldown_merges_two_moments_into_one(two_distinct_moments):
    """Cooldown is a configuration choice, and replay honours whichever is given."""
    from dataclasses import replace

    patient = replace(DEFAULT_CONFIG, moment_cooldown_seconds=600)

    result = replay_samples(two_distinct_moments, config=patient)

    assert len(result.candidates) == 1
    # The second burst is still visible as a crossing; only creation was suppressed.
    assert len(result.threshold_crossings) > 1


def test_cooldown_uses_the_same_boundary_as_production():
    """`services.is_in_cooldown` blocks on `detected_at > now - cooldown`.

    A crossing exactly one cooldown after a candidate is therefore eligible; one
    a second earlier is not. Replay must agree, or evaluation would predict a
    different candidate count than production produces.
    """
    from dataclasses import replace

    config = replace(DEFAULT_CONFIG, moment_cooldown_seconds=30)

    # Two bursts exactly 30 seconds apart, with calm either side.
    samples = ordered(
        calm_baseline(start=0.0, end=120.0),
        hype_burst(start=120.0, end=126.0, chatter_prefix="a"),
        calm_baseline(start=126.0, end=150.0),
        hype_burst(start=150.0, end=156.0, chatter_prefix="b"),
        calm_baseline(start=156.0, end=220.0),
    )

    result = replay_samples(samples, config=config)
    stamps = result.candidate_timestamps

    assert len(stamps) >= 2
    assert (stamps[1] - stamps[0]) > timedelta(seconds=config.moment_cooldown_seconds)


# -- observation contents -----------------------------------------------------


def test_an_observation_carries_only_aggregates(single_spike):
    """No text, no chatter hash: the report is built straight from these."""
    observation = replay_samples(single_spike).observations[0]
    fields = set(vars(observation))

    assert not any("text" in name or "hash" in name or "chatter_hash" == name for name in fields)
    assert "current_unique_chatter_count" in fields
    assert "current_message_count" in fields


def test_both_windows_are_reported(single_spike):
    result = replay_samples(single_spike)
    busy = max(result.observations, key=lambda obs: obs.total_score)

    assert busy.current_message_count > 0
    assert busy.baseline_message_count > 0
    assert busy.current_unique_chatter_count > 0
    assert busy.current_emote_count > 0
    assert busy.current_reaction_count > 0
