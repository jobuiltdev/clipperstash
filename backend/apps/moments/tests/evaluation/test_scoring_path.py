"""Replay must hand the detector the samples it was given, unaltered.

The architectural requirement is one scoring truth. An evaluator that
pre-processes text — even in a way that provably preserves today's signals — has
formed an opinion about detector-internal semantics, and a signal added later
that read text differently would drift away from production without anything
failing.

So these tests do not check that a transformation is *correct*. They check that
there is no transformation: the exact `ChatSample` objects reach `score_samples`,
and the evaluation layer contains no knowledge of what text means.
"""

from __future__ import annotations

import inspect

import pytest

from apps.moments.detector import DEFAULT_CONFIG
from apps.moments.detector.detector import score_samples
from apps.moments.evaluation import replay as replay_module
from apps.moments.evaluation.replay import replay_samples

from .conftest import chat, ordered

# Texts that exercise the lexicon in every direction: hits, phrases, elongation,
# near-misses that must not match, punctuation, emptiness and unicode.
CORPUS = [
    "LMAO no way",
    "lolllll",
    "yoooooo",
    "whatever",
    "what the",
    "hello everyone",
    "",
    "clip it",
    "clipping through the wall",
    "POGGERS POGGERS POGGERS",
    "oh my god",
    "holy",
    "holy shit",
    "w",
    "www",
    "ez win",
    "他很厉害",
    "  ",
    "no-way",
    "WHAT?!",
]


def corpus_samples():
    """One message per corpus entry, all landing inside a single window."""
    return [
        sample
        for index, text in enumerate(CORPUS)
        for sample in chat(
            start=index * 0.4,
            end=index * 0.4 + 0.2,
            count=1,
            chatters=1,
            text=text,
            emotes=index % 3,
            chatter_prefix=f"corpus{index}",
        )
    ]


# -- the samples reach the detector untouched ---------------------------------


def test_the_detector_receives_the_original_sample_objects(monkeypatch):
    """Identity, not equality. Nothing is rebuilt on the way through."""
    samples = corpus_samples()
    seen: list[list] = []
    real = replay_module.score_samples

    def recording(passed, evaluation_time, *, config):
        seen.append(list(passed))
        return real(passed, evaluation_time, config=config)

    monkeypatch.setattr(replay_module, "score_samples", recording)

    replay_samples(samples, cadence_seconds=1.0)

    assert seen
    by_identity = {id(sample) for window in seen for sample in window}
    assert by_identity <= {id(sample) for sample in samples}
    for window in seen:
        for sample in window:
            assert any(sample is original for original in samples)


def test_the_detector_receives_the_original_text(monkeypatch):
    """The specific failure the substitution introduced."""
    samples = corpus_samples()
    seen_texts: set[str] = set()
    real = replay_module.score_samples

    def recording(passed, evaluation_time, *, config):
        window = list(passed)
        seen_texts.update(sample.text for sample in window)
        return real(window, evaluation_time, config=config)

    monkeypatch.setattr(replay_module, "score_samples", recording)

    replay_samples(samples, cadence_seconds=1.0)

    assert seen_texts == set(CORPUS)


def test_replay_holds_no_knowledge_of_reaction_text():
    """The evaluation layer must not import or name the text-reading machinery."""
    source = inspect.getsource(replay_module)

    assert "has_reaction" not in source
    assert "normalize_tokens" not in source
    assert "reaction_tokens" not in source
    assert "reaction_phrases" not in source
    assert "elongatable_tokens" not in source


def test_no_evaluation_module_reads_message_text_semantics():
    """Across the whole package, not just replay."""
    from apps.moments.evaluation import (
        comparison,
        config,
        errors,
        labels,
        matching,
        metrics,
        report,
    )

    for module in (replay_module, comparison, config, errors, labels, matching, metrics, report):
        source = inspect.getsource(module)
        assert "has_reaction" not in source, module.__name__
        assert "normalize_tokens" not in source, module.__name__


def test_replay_defines_no_sample_transformation():
    """Nothing rebuilds a `ChatSample` on the way to the detector."""
    source = inspect.getsource(replay_module)

    # `ChatSample` is imported for typing; it must never be constructed here
    # except when reading rows out of the database.
    construction_sites = source.count("ChatSample(")
    assert construction_sites == 1, "the only ChatSample() is in load_session_samples"
    assert "load_session_samples" in source


# -- and the answer is unchanged ----------------------------------------------


def test_replay_matches_direct_scoring_over_the_whole_corpus():
    samples = ordered(
        corpus_samples(),
        chat(start=20.0, end=30.0, count=30, chatters=20, text="LMAO no way clip it", emotes=2),
    )

    result = replay_samples(samples, cadence_seconds=1.0)

    assert result.observations
    for observation in result.observations:
        direct = score_samples(samples, observation.evaluated_at, config=DEFAULT_CONFIG)
        assert observation.total_score == direct.total
        assert observation.reaction_score == direct.components.reaction
        assert observation.current_reaction_count == direct.current.reaction_message_count
        assert observation.baseline_reaction_count == direct.baseline.reaction_message_count


def test_a_narrower_lexicon_is_honoured_end_to_end():
    """An experimental configuration changes the detector's reading, not replay's."""
    from dataclasses import replace

    only_poggers = replace(DEFAULT_CONFIG, reaction_tokens=frozenset({"poggers"}))

    samples = chat(start=0.0, end=10.0, count=20, chatters=10, text="lol")

    default_run = replay_samples(samples, config=DEFAULT_CONFIG, cadence_seconds=1.0)
    narrow_run = replay_samples(samples, config=only_poggers, cadence_seconds=1.0)

    assert max(obs.current_reaction_count for obs in default_run.observations) > 0
    assert max(obs.current_reaction_count for obs in narrow_run.observations) == 0


@pytest.mark.parametrize("text", CORPUS)
def test_every_corpus_entry_scores_the_same_through_replay_as_directly(text):
    samples = chat(start=0.0, end=10.0, count=12, chatters=6, text=text, emotes=1)

    result = replay_samples(samples, cadence_seconds=5.0)

    for observation in result.observations:
        direct = score_samples(samples, observation.evaluated_at, config=DEFAULT_CONFIG)
        assert observation.total_score == direct.total
        assert observation.current_reaction_count == direct.current.reaction_message_count


def test_the_corpus_exercises_both_reaction_verdicts():
    """A guard on the guard: an all-reaction corpus would prove nothing."""
    from apps.moments.detector.signals import has_reaction

    assert {has_reaction(text, config=DEFAULT_CONFIG) for text in CORPUS} == {True, False}
