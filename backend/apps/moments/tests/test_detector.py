"""Tests for the pure detector.

No database, no clock, no network: every evaluation time is explicit, so every
assertion here is deterministic.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from apps.moments.detector import DEFAULT_CONFIG, ChatSample, DetectorInputError, score_samples
from apps.moments.detector.detector import passes_activity_gate
from apps.moments.detector.scorer import (
    absolute_activity_score,
    clamp,
    component_scores,
    diversity_score,
    emote_score,
    reaction_score,
    total_score,
    velocity_ratio,
    velocity_score,
)
from apps.moments.detector.signals import extract_signals, has_reaction, normalize_tokens
from apps.moments.detector.window import build_windows, split_samples

T = datetime(2026, 9, 4, 18, 0, 0, tzinfo=UTC)


def sample(seconds_before: float, chatter: str = "a", text: str = "hello", emotes: int = 0):
    return ChatSample(
        timestamp=T - timedelta(seconds=seconds_before),
        chatter_hash=chatter,
        text=text,
        emote_count=emotes,
    )


def burst(
    count: int,
    *,
    unique: int,
    window_start: float,
    window_end: float,
    text: str = "hello",
    emotes: int = 0,
) -> list[ChatSample]:
    """`count` messages spread across a span, from `unique` distinct chatters."""
    if count == 0:
        return []
    step = (window_start - window_end) / count
    return [
        sample(
            window_start - step * index - step / 2,
            chatter=f"chatter-{index % unique}",
            text=text,
            emotes=emotes,
        )
        for index in range(count)
    ]


def current_burst(count: int, **kwargs) -> list[ChatSample]:
    return burst(count, window_start=10, window_end=0, **kwargs)


def baseline_burst(count: int, **kwargs) -> list[ChatSample]:
    return burst(count, window_start=70, window_end=10, **kwargs)


def signals_for(samples, *, duration: float):
    return extract_signals(samples, duration_seconds=duration)


# ============================================================== windowing ====


def test_window_boundaries_follow_the_evaluation_time():
    bounds = build_windows(T)

    assert bounds.current_end == T
    assert bounds.current_start == T - timedelta(seconds=10)
    assert bounds.baseline_end == T - timedelta(seconds=10)
    assert bounds.baseline_start == T - timedelta(seconds=70)
    assert bounds.current_seconds == 10
    assert bounds.baseline_seconds == 60


def test_the_windows_abut_and_never_overlap():
    bounds = build_windows(T)

    assert bounds.baseline_end == bounds.current_start
    probe = bounds.current_start
    assert bounds.contains_baseline(probe) is True
    assert bounds.contains_current(probe) is False


def test_a_message_on_the_shared_boundary_is_counted_exactly_once():
    boundary = sample(10)
    windowed = split_samples([boundary], build_windows(T))

    assert len(windowed.baseline) == 1
    assert len(windowed.current) == 0


def test_a_message_at_the_evaluation_instant_is_current():
    windowed = split_samples([sample(0)], build_windows(T))

    assert len(windowed.current) == 1
    assert len(windowed.baseline) == 0


def test_a_message_on_the_far_baseline_edge_is_excluded():
    """The baseline is half-open too, so its own start belongs to neither."""
    windowed = split_samples([sample(70)], build_windows(T))

    assert windowed.current == ()
    assert windowed.baseline == ()


@pytest.mark.parametrize("seconds_before", [70.1, 120, 3600])
def test_messages_older_than_both_windows_are_ignored(seconds_before):
    windowed = split_samples([sample(seconds_before)], build_windows(T))

    assert windowed.current == ()
    assert windowed.baseline == ()


def test_messages_after_the_evaluation_time_are_ignored():
    windowed = split_samples([sample(-5)], build_windows(T))

    assert windowed.current == ()
    assert windowed.baseline == ()


def test_samples_are_returned_in_timestamp_order():
    windowed = split_samples([sample(1), sample(9), sample(5)], build_windows(T))

    timestamps = [entry.timestamp for entry in windowed.current]
    assert timestamps == sorted(timestamps)


def test_a_naive_evaluation_time_is_rejected():
    with pytest.raises(DetectorInputError, match="timezone-aware"):
        build_windows(datetime(2026, 9, 4, 18, 0, 0))


def test_a_naive_sample_timestamp_is_rejected():
    naive = ChatSample(timestamp=datetime(2026, 9, 4, 17, 59, 55), chatter_hash="a", text="hi")

    with pytest.raises(DetectorInputError, match="timezone-aware"):
        split_samples([naive], build_windows(T))


def test_window_lengths_follow_the_configuration():
    config = replace(DEFAULT_CONFIG, current_window_seconds=5, baseline_window_seconds=30)

    bounds = build_windows(T, config=config)

    assert bounds.current_seconds == 5
    assert bounds.baseline_seconds == 30


# =============================================================== velocity ====


def test_flat_activity_scores_low():
    current = signals_for(current_burst(5, unique=5), duration=10)
    baseline = signals_for(baseline_burst(30, unique=10), duration=60)

    assert velocity_ratio(current, baseline) == pytest.approx(1.0)
    assert velocity_score(current, baseline) == 0.0


def test_a_clear_spike_scores_higher_than_flat_activity():
    baseline = signals_for(baseline_burst(30, unique=10), duration=60)
    flat = signals_for(current_burst(5, unique=5), duration=10)
    spike = signals_for(current_burst(25, unique=20), duration=10)

    assert velocity_score(spike, baseline) > velocity_score(flat, baseline)


def test_velocity_rises_monotonically_with_the_burst():
    baseline = signals_for(baseline_burst(30, unique=10), duration=60)
    scores = [
        velocity_score(signals_for(current_burst(count, unique=count), duration=10), baseline)
        for count in (5, 10, 15, 20, 30)
    ]

    assert scores == sorted(scores)


def test_a_zero_baseline_does_not_divide_by_zero():
    current = signals_for(current_burst(10, unique=10), duration=10)
    baseline = signals_for([], duration=60)

    ratio = velocity_ratio(current, baseline)

    assert ratio == pytest.approx(1.0 / DEFAULT_CONFIG.min_baseline_rate_per_second)
    assert 0.0 <= velocity_score(current, baseline) <= 1.0


def test_one_message_against_silence_is_not_viral():
    current = signals_for(current_burst(1, unique=1), duration=10)
    baseline = signals_for([], duration=60)

    score = velocity_score(current, baseline)

    assert score < 0.1, "a single message cannot carry a ratio"


def test_a_large_burst_against_silence_does_register():
    current = signals_for(current_burst(30, unique=25), duration=10)
    baseline = signals_for([], duration=60)

    assert velocity_score(current, baseline) == pytest.approx(1.0)


def test_a_bigger_burst_beats_a_trivial_one_at_the_same_ratio():
    """Both are far above baseline; only one has the volume to mean it."""
    baseline = signals_for([], duration=60)
    trivial = signals_for(current_burst(2, unique=2), duration=10)
    substantial = signals_for(current_burst(20, unique=15), duration=10)

    assert velocity_score(substantial, baseline) > velocity_score(trivial, baseline)


@pytest.mark.parametrize("count", [0, 1, 5, 50, 500])
def test_velocity_stays_within_bounds(count):
    current = signals_for(current_burst(count, unique=max(1, count)), duration=10)
    baseline = signals_for([], duration=60)

    assert 0.0 <= velocity_score(current, baseline) <= 1.0


def test_an_empty_current_window_scores_zero_velocity():
    current = signals_for([], duration=10)
    baseline = signals_for(baseline_burst(30, unique=10), duration=60)

    assert velocity_score(current, baseline) == 0.0


# ============================================================== diversity ====


def test_more_unique_chatters_score_higher_at_the_same_volume():
    few = signals_for(current_burst(20, unique=2), duration=10)
    many = signals_for(current_burst(20, unique=20), duration=10)

    assert diversity_score(many) > diversity_score(few)


def test_one_chatter_spamming_scores_low():
    solo = signals_for(current_burst(20, unique=1), duration=10)

    assert diversity_score(solo) < 0.2


def test_pseudonymous_hashes_serve_as_identities():
    samples = [
        sample(5, chatter="hash-a"),
        sample(4, chatter="hash-b"),
        sample(3, chatter="hash-a"),
    ]
    signals = signals_for(samples, duration=10)

    assert signals.unique_chatters == 2


@pytest.mark.parametrize(("count", "unique"), [(0, 1), (1, 1), (20, 1), (30, 30), (200, 100)])
def test_diversity_stays_within_bounds(count, unique):
    signals = signals_for(current_burst(count, unique=unique), duration=10)

    assert 0.0 <= diversity_score(signals) <= 1.0


# ================================================================= emotes ====


def test_no_emotes_scores_zero():
    signals = signals_for(current_burst(20, unique=20, emotes=0), duration=10)

    assert emote_score(signals) == 0.0


def test_higher_emote_density_scores_higher():
    light = signals_for(current_burst(20, unique=20, emotes=1), duration=10)
    heavy = signals_for(current_burst(20, unique=20, emotes=4), duration=10)

    assert emote_score(heavy) > emote_score(light)


def test_multiple_emotes_per_message_are_counted():
    signals = signals_for(current_burst(10, unique=10, emotes=3), duration=10)

    assert signals.emote_count == 30
    assert signals.emotes_per_message == pytest.approx(3.0)
    assert signals.messages_with_emotes == 10


def test_partial_emote_coverage_scores_between_none_and_all():
    mixed = signals_for(
        current_burst(10, unique=10, emotes=0) + [sample(2, chatter="x", emotes=2)],
        duration=10,
    )
    none = signals_for(current_burst(11, unique=11, emotes=0), duration=10)
    every = signals_for(current_burst(11, unique=11, emotes=2), duration=10)

    assert emote_score(none) < emote_score(mixed) < emote_score(every)


@pytest.mark.parametrize("emotes", [0, 1, 5, 50])
def test_emote_score_stays_within_bounds(emotes):
    signals = signals_for(current_burst(10, unique=10, emotes=emotes), duration=10)

    assert 0.0 <= emote_score(signals) <= 1.0


def test_negative_emote_counts_are_treated_as_none():
    signals = signals_for([sample(5, emotes=-3)], duration=10)

    assert signals.emote_count == 0
    assert signals.messages_with_emotes == 0


# ====================================================== reaction language ====


@pytest.mark.parametrize(
    "text",
    ["lol", "LMAO", "rofl", "omg", "wtf", "what", "insane", "crazy", "clip", "huge", "bruh"],
)
def test_reaction_tokens_are_recognized(text):
    assert has_reaction(text) is True


@pytest.mark.parametrize("text", ["no way", "clip it", "clip that", "oh my god", "NO WAY!!"])
def test_reaction_phrases_are_recognized(text):
    assert has_reaction(text) is True


@pytest.mark.parametrize("text", ["LOL", "Lol", "lOl", "OMG"])
def test_casing_is_normalized(text):
    assert has_reaction(text) is True


@pytest.mark.parametrize("text", ["what?!", "lol...", "(omg)", "wtf,", "-lol-"])
def test_punctuation_does_not_prevent_a_match(text):
    assert has_reaction(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "whatever happened there",
        "clipboard",
        "lollipop",
        "somewhat unusual",
        "wow" + "ser",
        "crazily",
        "the wall",
    ],
)
def test_an_unrelated_substring_does_not_match(text):
    assert has_reaction(text) is False, f"{text!r} must not read as a reaction"


@pytest.mark.parametrize("text", ["lollll", "yoooo", "wowwww", "omgggg", "nooooo way"])
def test_intended_elongation_is_normalized(text):
    assert has_reaction(text) is True


@pytest.mark.parametrize("text", ["hmmm", "aaaah", "brrrr", "shhhh"])
def test_unrelated_elongation_is_not_forced_into_a_match(text):
    assert has_reaction(text) is False


@pytest.mark.parametrize("text", ["", "   ", "!!!", "12345", "..."])
def test_empty_or_symbolic_messages_do_not_react(text):
    assert has_reaction(text) is False


def test_tokens_collapse_only_for_listed_words():
    """Elongation is undone only where the lexicon expects it."""
    assert normalize_tokens("lollll") == ["lol"]
    assert normalize_tokens("yoooo") == ["yo"]
    assert normalize_tokens("brrrr") == ["brrrr"], "an unlisted word is left as written"
    assert normalize_tokens("cool") == ["cool"], "ordinary doubled letters are untouched"


def test_a_phrase_must_be_consecutive():
    assert has_reaction("no it went that way") is False


def test_reaction_spam_from_one_chatter_does_not_beat_a_real_crowd():
    spam = signals_for(current_burst(20, unique=1, text="LMAO"), duration=10)
    crowd = signals_for(current_burst(20, unique=20, text="LMAO"), duration=10)

    assert reaction_score(crowd) > reaction_score(spam)
    assert reaction_score(spam) < 0.3


def test_reaction_score_rises_with_the_share_reacting():
    quiet = signals_for(current_burst(20, unique=20, text="hello there"), duration=10)
    loud = signals_for(current_burst(20, unique=20, text="omg no way"), duration=10)

    assert reaction_score(loud) > reaction_score(quiet)
    assert reaction_score(quiet) == 0.0


@pytest.mark.parametrize(("count", "unique"), [(0, 1), (1, 1), (20, 1), (40, 40)])
def test_reaction_score_stays_within_bounds(count, unique):
    signals = signals_for(current_burst(count, unique=unique, text="lmao"), duration=10)

    assert 0.0 <= reaction_score(signals) <= 1.0


def test_the_lexicon_is_configurable():
    config = replace(DEFAULT_CONFIG, reaction_tokens=frozenset({"bananas"}))

    assert has_reaction("that was bananas", config=config) is True
    assert has_reaction("lol", config=config) is False


# ========================================================= absolute volume ====


def test_absolute_activity_rises_with_volume_and_saturates():
    scores = [
        absolute_activity_score(signals_for(current_burst(count, unique=count), duration=10))
        for count in (0, 5, 15, 30, 60)
    ]

    assert scores == sorted(scores)
    assert scores[0] == 0.0
    assert scores[-2] == pytest.approx(1.0)
    assert scores[-1] == pytest.approx(1.0)


# =============================================================== total ======


def test_the_weights_sum_to_one():
    assert sum(DEFAULT_CONFIG.weights().values()) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "component",
    ["velocity", "reaction", "diversity", "emote", "absolute_activity"],
)
def test_each_component_moves_the_total_in_its_own_direction(component):
    from apps.moments.detector.scorer import ComponentScores

    low = ComponentScores(0.0, 0.0, 0.0, 0.0, 0.0)
    high = replace(low, **{component: 1.0})

    assert total_score(high) > total_score(low)
    assert total_score(high) == pytest.approx(DEFAULT_CONFIG.weights()[component] * 100)


def test_the_total_is_bounded():
    from apps.moments.detector.scorer import ComponentScores

    assert total_score(ComponentScores(0.0, 0.0, 0.0, 0.0, 0.0)) == 0.0
    assert total_score(ComponentScores(1.0, 1.0, 1.0, 1.0, 1.0)) == pytest.approx(100.0)


def test_a_strong_moment_outscores_a_weak_one():
    baseline = baseline_burst(30, unique=12)
    weak = baseline + current_burst(8, unique=4, text="hello", emotes=0)
    strong = baseline + current_burst(30, unique=25, text="omg no way that was insane", emotes=2)

    assert score_samples(strong, T).total > score_samples(weak, T).total


def test_quiet_chat_does_not_qualify():
    samples = baseline_burst(20, unique=8) + current_burst(2, unique=2)

    result = score_samples(samples, T)

    assert result.qualifies is False
    assert result.activity_gate_passed is False


def test_a_broad_reaction_burst_qualifies():
    samples = baseline_burst(30, unique=12) + current_burst(
        30, unique=25, text="LMAO no way clip it", emotes=2
    )

    result = score_samples(samples, T)

    assert result.activity_gate_passed is True
    assert result.total >= DEFAULT_CONFIG.candidate_threshold
    assert result.qualifies is True


def test_the_result_carries_its_full_working():
    samples = baseline_burst(30, unique=12) + current_burst(20, unique=15, text="omg", emotes=1)

    result = score_samples(samples, T)

    assert result.current.message_count == 20
    assert result.current.unique_chatters == 15
    assert result.baseline.message_count == 30
    assert result.velocity_ratio > 1
    assert set(result.components.as_dict()) == {
        "velocity",
        "reaction",
        "diversity",
        "emote",
        "absolute_activity",
    }
    assert result.threshold == DEFAULT_CONFIG.candidate_threshold
    assert result.evaluation_time == T


def test_scoring_is_deterministic():
    samples = baseline_burst(30, unique=12) + current_burst(25, unique=20, text="omg", emotes=1)

    first = score_samples(samples, T)
    second = score_samples(list(reversed(samples)), T)

    assert first.total == second.total
    assert first.components == second.components


# ========================================================== activity gate ====


def test_too_few_messages_fails_the_gate():
    signals = signals_for(current_burst(4, unique=4), duration=10)

    assert passes_activity_gate(signals) is False


def test_too_few_unique_chatters_fails_the_gate():
    signals = signals_for(current_burst(20, unique=2), duration=10)

    assert passes_activity_gate(signals) is False


def test_the_gate_passes_exactly_at_the_minimums():
    signals = signals_for(current_burst(5, unique=3), duration=10)

    assert signals.message_count == DEFAULT_CONFIG.min_current_messages
    assert signals.unique_chatters == DEFAULT_CONFIG.min_current_unique_chatters
    assert passes_activity_gate(signals) is True


def test_passing_the_gate_does_not_by_itself_qualify():
    samples = baseline_burst(60, unique=20) + current_burst(6, unique=5, text="hello")

    result = score_samples(samples, T)

    assert result.activity_gate_passed is True
    assert result.total < DEFAULT_CONFIG.candidate_threshold
    assert result.qualifies is False


def test_a_failed_gate_blocks_a_high_score():
    """Diagnostics are still produced; only the verdict is withheld."""
    config = replace(DEFAULT_CONFIG, min_current_messages=100)
    samples = baseline_burst(30, unique=12) + current_burst(
        30, unique=25, text="LMAO no way", emotes=2
    )

    result = score_samples(samples, T, config=config)

    assert result.total > config.candidate_threshold
    assert result.activity_gate_passed is False
    assert result.qualifies is False


# =============================================================== threshold ===


def _score_with_threshold(threshold: float):
    config = replace(DEFAULT_CONFIG, candidate_threshold=threshold)
    samples = baseline_burst(30, unique=12) + current_burst(
        30, unique=25, text="LMAO no way clip it", emotes=2
    )
    return score_samples(samples, T, config=config)


def test_below_the_threshold_does_not_qualify():
    reference = _score_with_threshold(DEFAULT_CONFIG.candidate_threshold)
    above = _score_with_threshold(reference.total + 0.01)

    assert above.qualifies is False


def test_exactly_at_the_threshold_qualifies():
    """The comparison is `>=`, so landing on the threshold counts."""
    reference = _score_with_threshold(DEFAULT_CONFIG.candidate_threshold)
    exact = _score_with_threshold(reference.total)

    assert exact.total == exact.threshold
    assert exact.qualifies is True


def test_above_the_threshold_qualifies():
    reference = _score_with_threshold(DEFAULT_CONFIG.candidate_threshold)
    below = _score_with_threshold(reference.total - 0.01)

    assert below.qualifies is True


# ================================================================ helpers ====


@pytest.mark.parametrize(
    ("value", "expected"), [(-1.0, 0.0), (0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (2.0, 1.0)]
)
def test_clamp_bounds_values(value, expected):
    assert clamp(value) == expected


def test_component_scores_are_rounded_but_faithful():
    current = signals_for(current_burst(17, unique=11, text="omg", emotes=1), duration=10)
    baseline = signals_for(baseline_burst(30, unique=12), duration=60)

    components = component_scores(current, baseline)

    for value in components.as_dict().values():
        assert 0.0 <= value <= 1.0
        assert round(value, 4) == value
