"""Pairing candidates with labels, one to one.

The property being defended is that a burst cannot flatter the numbers. Three
candidates seconds apart have found one moment; matching must record one true
positive and two false positives, not three true positives.
"""

from __future__ import annotations

from apps.moments.evaluation.labels import Label, LabelSet
from apps.moments.evaluation.matching import match_candidates

from .conftest import at


def label(seconds: float, *, tolerance: float = 10.0) -> Label:
    return Label(timestamp=at(seconds), tolerance_seconds=tolerance)


def label_set(*labels: Label) -> LabelSet:
    return LabelSet(session_id=1, default_tolerance_seconds=10.0, labels=tuple(labels))


# -- the basic cases -----------------------------------------------------------


def test_an_exact_timestamp_matches():
    result = match_candidates(label_set(label(100)), [at(100)])

    assert result.true_positives == 1
    assert result.matches[0].delta_seconds == 0.0


def test_a_candidate_inside_the_tolerance_matches():
    result = match_candidates(label_set(label(100, tolerance=10)), [at(107)])

    assert result.true_positives == 1
    assert result.matches[0].delta_seconds == 7.0


def test_a_candidate_exactly_on_the_boundary_matches():
    result = match_candidates(label_set(label(100, tolerance=10)), [at(110)])

    assert result.true_positives == 1


def test_a_candidate_outside_the_tolerance_does_not_match():
    result = match_candidates(label_set(label(100, tolerance=10)), [at(111)])

    assert result.true_positives == 0
    assert result.false_positives == 1
    assert result.false_negatives == 1


def test_an_early_candidate_matches_too():
    """The detector can fire before the instant a person wrote down."""
    result = match_candidates(label_set(label(100, tolerance=10)), [at(94)])

    assert result.true_positives == 1
    assert result.matches[0].delta_seconds == -6.0
    assert result.matches[0].absolute_delta_seconds == 6.0


def test_each_label_uses_its_own_tolerance():
    result = match_candidates(
        label_set(label(100, tolerance=5), label(200, tolerance=30)),
        [at(108), at(225)],
    )

    assert result.true_positives == 1
    assert result.matches[0].label.timestamp == at(200)


# -- one to one ----------------------------------------------------------------


def test_one_label_cannot_absorb_a_whole_burst():
    """The property that stops a burst inflating recall and precision at once."""
    result = match_candidates(label_set(label(100, tolerance=10)), [at(98), at(100), at(102)])

    assert result.true_positives == 1
    assert result.false_positives == 2
    assert result.false_negatives == 0


def test_one_candidate_cannot_answer_two_labels():
    result = match_candidates(
        label_set(label(100, tolerance=30), label(120, tolerance=30)),
        [at(110)],
    )

    assert result.true_positives == 1
    assert result.false_negatives == 1
    assert result.false_positives == 0


def test_the_nearest_candidate_wins():
    result = match_candidates(label_set(label(100, tolerance=20)), [at(115), at(102)])

    assert result.matches[0].candidate == at(102)
    assert result.unmatched_candidates == (at(115),)


def test_the_nearest_label_wins():
    result = match_candidates(
        label_set(label(100, tolerance=30), label(130, tolerance=30)),
        [at(128)],
    )

    assert result.matches[0].label.timestamp == at(130)


def test_a_second_candidate_falls_through_to_the_next_label():
    """Greedy nearest still pairs both when both are genuinely present."""
    result = match_candidates(
        label_set(label(100, tolerance=30), label(130, tolerance=30)),
        [at(102), at(128)],
    )

    assert result.true_positives == 2
    assert [match.candidate for match in result.matches] == [at(102), at(128)]


# -- determinism ---------------------------------------------------------------


def test_an_exact_tie_resolves_deterministically():
    """Equidistant either side. The tie-break is documented, not arbitrary."""
    result = match_candidates(label_set(label(100, tolerance=20)), [at(90), at(110)])

    first = match_candidates(label_set(label(100, tolerance=20)), [at(110), at(90)])

    assert result.matches[0].candidate == first.matches[0].candidate


def test_candidate_order_does_not_change_the_matching():
    labels = label_set(label(100, tolerance=15), label(200, tolerance=15))
    forward = match_candidates(labels, [at(98), at(205)])
    reversed_order = match_candidates(labels, [at(205), at(98)])

    assert forward == reversed_order


def test_two_labels_competing_for_one_candidate_resolve_by_distance():
    result = match_candidates(
        label_set(label(100, tolerance=40), label(140, tolerance=40)),
        [at(135)],
    )

    assert result.matches[0].label.timestamp == at(140)


# -- degenerate input ----------------------------------------------------------


def test_no_candidates_makes_every_label_a_miss():
    result = match_candidates(label_set(label(100), label(200)), [])

    assert result.true_positives == 0
    assert result.false_negatives == 2
    assert result.false_positives == 0


def test_no_labels_makes_every_candidate_a_false_positive():
    result = match_candidates(label_set(), [at(100), at(200)])

    assert result.true_positives == 0
    assert result.false_positives == 2
    assert result.false_negatives == 0


def test_nothing_on_either_side_is_empty_rather_than_an_error():
    result = match_candidates(label_set(), [])

    assert result.matches == ()
    assert result.unmatched_labels == ()
    assert result.unmatched_candidates == ()


def test_matches_are_reported_in_label_order():
    result = match_candidates(
        label_set(label(300), label(100), label(200)),
        [at(300), at(100), at(200)],
    )

    assert [match.label.timestamp for match in result.matches] == [at(100), at(200), at(300)]
