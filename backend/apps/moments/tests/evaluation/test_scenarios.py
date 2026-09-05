"""Does the detector behave sensibly on chat shaped like real stream behaviour?

Milestone 5 asked whether the arithmetic was right. These ask the calibration
question: given a stream doing a recognizable thing, does the detector reach the
conclusion a person would? They are stated as claims about *behaviour* — "a wall
of copy-paste from four people is not a crowd reaction" — rather than as exact
numbers, so they survive a deliberate recalibration while still failing if a
signal stops working.

Every scenario is synthetic. No real chat is used anywhere in this package.
"""

from __future__ import annotations

from apps.moments.detector import DEFAULT_CONFIG
from apps.moments.evaluation.replay import replay_samples

from .conftest import PLAIN_TEXT, REACTION_TEXT, calm_baseline, chat, hype_burst, ordered


def peak(samples, **kwargs):
    """The highest-scoring observation of one replay.

    Warm-up ticks are excluded throughout this module. In the first
    baseline-plus-current seconds of any collection the baseline window reaches
    back before the first message, so velocity is inflated by an artefact of
    when monitoring started. That is a real property worth reporting — the CLI
    does — but it says nothing about whether the detector recognises stream
    behaviour, which is what these scenarios ask.
    """
    result = replay_samples(samples, require_complete_baseline=True, **kwargs)
    return max(result.observations, key=lambda obs: obs.total_score)


def steady(samples, **kwargs):
    """One replay, warm-up excluded."""
    return replay_samples(samples, require_complete_baseline=True, **kwargs)


# -- quiet stream -------------------------------------------------------------


def test_a_quiet_stream_produces_nothing(quiet_stream):
    result = steady(quiet_stream)

    assert result.candidates == ()
    assert result.threshold_crossings == ()
    assert max(obs.total_score for obs in result.observations) < DEFAULT_CONFIG.candidate_threshold


# -- sudden spike -------------------------------------------------------------


def test_a_sudden_spike_after_a_quiet_baseline_is_found(single_spike):
    result = steady(single_spike)

    assert len(result.candidates) == 1
    best = max(result.observations, key=lambda obs: obs.total_score)
    assert best.total_score >= DEFAULT_CONFIG.candidate_threshold
    assert best.velocity_ratio > 1.0


def test_the_spike_raises_the_velocity_signal_specifically(single_spike, quiet_stream):
    """Velocity is the signal that is supposed to notice a change of pace."""
    assert peak(single_spike).velocity_score > peak(quiet_stream).velocity_score


# -- reaction burst -----------------------------------------------------------


def test_reaction_language_and_emotes_raise_their_own_signals():
    """Same volume and breadth; only what people are typing differs."""
    plain = ordered(
        calm_baseline(start=0.0, end=120.0),
        chat(start=120.0, end=130.0, count=40, chatters=30, text=PLAIN_TEXT, chatter_prefix="p"),
    )
    reacting = ordered(
        calm_baseline(start=0.0, end=120.0),
        chat(
            start=120.0,
            end=130.0,
            count=40,
            chatters=30,
            text=REACTION_TEXT,
            emotes=3,
            chatter_prefix="p",
        ),
    )

    plain_peak = peak(plain)
    reacting_peak = peak(reacting)

    assert reacting_peak.reaction_score > plain_peak.reaction_score
    assert reacting_peak.emote_score > plain_peak.emote_score
    assert reacting_peak.total_score > plain_peak.total_score


# -- high volume, low diversity ----------------------------------------------


def test_a_few_people_spamming_is_not_a_crowd_reaction():
    """The case diversity exists to catch.

    Identical volume, identical language, identical emotes — the only difference
    is that one is thirty people reacting and the other is four people flooding.
    """
    broad = ordered(
        calm_baseline(start=0.0, end=120.0),
        chat(
            start=120.0,
            end=130.0,
            count=60,
            chatters=30,
            text=REACTION_TEXT,
            emotes=3,
            chatter_prefix="broad",
        ),
    )
    spam = ordered(
        calm_baseline(start=0.0, end=120.0),
        chat(
            start=120.0,
            end=130.0,
            count=60,
            chatters=4,
            text=REACTION_TEXT,
            emotes=3,
            chatter_prefix="spam",
        ),
    )

    broad_peak = peak(broad)
    spam_peak = peak(spam)

    assert spam_peak.diversity_score < broad_peak.diversity_score
    assert spam_peak.reaction_score < broad_peak.reaction_score
    assert spam_peak.total_score < broad_peak.total_score


def test_three_chatters_flooding_still_clears_the_baseline_threshold():
    """A calibration finding, recorded rather than tuned away.

    Sixty emote-laden messages in ten seconds from three people is not a crowd
    reaction, but the baseline calibration accepts it: velocity, emote and raw
    activity all saturate and carry 0.65 of the weight between them, while
    diversity — the one signal that objects — carries 0.15 and cannot pull the
    total back under 70.

    This is exactly the kind of thing Milestone 8 exists to surface. The default
    is deliberately left alone: changing it is a production calibration decision
    that needs evidence from real sessions, not one synthetic scenario. The test
    asserts today's behaviour so the finding stays visible and so a future
    recalibration has to acknowledge it.
    """
    spam = ordered(
        calm_baseline(start=0.0, end=120.0),
        chat(
            start=120.0,
            end=130.0,
            count=60,
            chatters=3,
            text=REACTION_TEXT,
            emotes=3,
            chatter_prefix="spam",
        ),
        calm_baseline(start=130.0, end=200.0),
    )

    result = steady(spam)
    best = max(result.observations, key=lambda obs: obs.total_score)

    assert len(result.candidates) == 1
    assert best.total_score >= DEFAULT_CONFIG.candidate_threshold
    # Diversity is the component that objected, and the only one that did.
    assert best.diversity_score < 0.4
    assert best.diversity_score == min(
        best.velocity_score,
        best.reaction_score,
        best.diversity_score,
        best.emote_score,
        best.absolute_activity_score,
    )
    assert best.velocity_score == 1.0
    assert best.emote_score > 0.9
    assert best.absolute_activity_score == 1.0


def test_weighting_diversity_higher_would_reject_that_flood():
    """The same chat, judged by an experimental calibration.

    Shown here because it is what the comparison tooling is for: a claim about a
    setting, checked against a scenario, without touching the production
    default. It is one synthetic case and is not evidence for promoting
    anything.
    """
    from dataclasses import replace

    diversity_led = replace(
        DEFAULT_CONFIG,
        velocity_weight=0.30,
        reaction_weight=0.15,
        diversity_weight=0.35,
        emote_weight=0.10,
        absolute_activity_weight=0.10,
    )

    spam = ordered(
        calm_baseline(start=0.0, end=120.0),
        chat(
            start=120.0,
            end=130.0,
            count=60,
            chatters=3,
            text=REACTION_TEXT,
            emotes=3,
            chatter_prefix="spam",
        ),
        calm_baseline(start=130.0, end=200.0),
    )
    broad = ordered(
        calm_baseline(start=0.0, end=120.0),
        hype_burst(start=120.0, end=130.0, chatter_prefix="broad"),
        calm_baseline(start=130.0, end=200.0),
    )

    assert steady(spam, config=diversity_led).candidates == ()
    # And it still finds the genuine crowd reaction, which is the part that matters.
    assert len(steady(broad, config=diversity_led).candidates) == 1


# -- sustained high chat ------------------------------------------------------


def test_sustained_high_activity_is_not_mistaken_for_a_spike():
    """A busy channel is busy all the time; that is its normal, not a moment.

    Velocity compares against the immediately preceding stretch, so a stream
    that is loud throughout has a ratio near one however loud it is.
    """
    sustained = chat(
        start=0.0,
        end=300.0,
        count=900,
        chatters=40,
        text=REACTION_TEXT,
        emotes=3,
        chatter_prefix="busy",
    )

    best = peak(sustained)

    assert best.velocity_ratio < 2.0
    assert best.velocity_score < 0.5


def test_a_spike_on_top_of_sustained_chat_still_stands_out():
    """The comparison is relative, so a loud channel can still have a louder moment."""
    busy = chat(
        start=0.0,
        end=300.0,
        count=600,
        chatters=40,
        text=PLAIN_TEXT,
        chatter_prefix="busy",
    )
    with_spike = ordered(
        busy,
        chat(
            start=200.0,
            end=210.0,
            count=120,
            chatters=60,
            text=REACTION_TEXT,
            emotes=4,
            chatter_prefix="hype",
        ),
    )

    assert peak(with_spike).velocity_ratio > peak(busy).velocity_ratio
    assert peak(with_spike).total_score > peak(busy).total_score


# -- cooldown burst -----------------------------------------------------------


def test_a_long_burst_shows_every_crossing_but_creates_one_candidate():
    """Calibration needs the crossings; production needs the single candidate."""
    long_burst = ordered(
        calm_baseline(start=0.0, end=120.0),
        hype_burst(start=120.0, end=150.0, chatter_prefix="long"),
        calm_baseline(start=150.0, end=220.0),
    )

    result = steady(long_burst)

    assert len(result.threshold_crossings) > 5
    assert len(result.candidates) == 1
    assert len(result.suppressed) == len(result.threshold_crossings) - 1


# -- second distinct moment ---------------------------------------------------


def test_two_events_past_the_cooldown_produce_two_candidates(two_distinct_moments):
    result = steady(two_distinct_moments)

    assert len(result.candidates) == 2
    assert all(obs.total_score >= DEFAULT_CONFIG.candidate_threshold for obs in result.candidates)
