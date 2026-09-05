"""Precision, recall, F1 and the rates that keep them honest.

The zero-denominator rules are tested as hard as the ordinary arithmetic. A
metrics table that prints `nan` teaches an operator to ignore it, and a vacuous
1.0 for a detector that never fires is worse than useless.
"""

from __future__ import annotations

import pytest

from apps.moments.evaluation.labels import Label, LabelSet
from apps.moments.evaluation.matching import match_candidates
from apps.moments.evaluation.metrics import (
    compute_metrics,
    f1_of,
    precision_of,
    rate_per_hour,
    recall_of,
)

from .conftest import at


def label(seconds: float, *, tolerance: float = 10.0) -> Label:
    return Label(timestamp=at(seconds), tolerance_seconds=tolerance)


def label_set(*labels: Label) -> LabelSet:
    return LabelSet(session_id=1, default_tolerance_seconds=10.0, labels=tuple(labels))


def metrics_for(labels: LabelSet, candidates, *, duration: float = 3600.0, crossings: int = 0):
    return compute_metrics(
        match_candidates(labels, candidates),
        ground_truth_moments=len(labels),
        predicted_moments=len(candidates),
        threshold_crossings=crossings or len(candidates),
        observations=int(duration),
        duration_seconds=duration,
    )


# -- the ordinary cases --------------------------------------------------------


def test_perfect_detection():
    metrics = metrics_for(label_set(label(100), label(200)), [at(100), at(200)])

    assert metrics.true_positives == 2
    assert metrics.false_positives == 0
    assert metrics.false_negatives == 0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.f1 == 1.0


def test_a_false_positive_lowers_precision_only():
    metrics = metrics_for(label_set(label(100)), [at(100), at(500)])

    assert metrics.precision == 0.5
    assert metrics.recall == 1.0
    assert metrics.f1 == pytest.approx(2 / 3)


def test_a_missed_moment_lowers_recall_only():
    metrics = metrics_for(label_set(label(100), label(500)), [at(100)])

    assert metrics.precision == 1.0
    assert metrics.recall == 0.5
    assert metrics.f1 == pytest.approx(2 / 3)


def test_a_mixed_result():
    metrics = metrics_for(
        label_set(label(100), label(200), label(300)),
        [at(100), at(205), at(900)],
    )

    assert metrics.true_positives == 2
    assert metrics.false_positives == 1
    assert metrics.false_negatives == 1
    assert metrics.precision == pytest.approx(2 / 3)
    assert metrics.recall == pytest.approx(2 / 3)


# -- zero denominators ---------------------------------------------------------


def test_predicting_nothing_scores_zero_not_one():
    """A detector that never fires has achieved nothing, not perfect precision."""
    metrics = metrics_for(label_set(label(100)), [])

    assert metrics.precision == 0.0
    assert metrics.recall == 0.0
    assert metrics.f1 == 0.0


def test_labels_with_no_matches_score_zero_recall():
    metrics = metrics_for(label_set(label(100)), [at(900)])

    assert metrics.recall == 0.0
    assert metrics.precision == 0.0
    assert metrics.f1 == 0.0


def test_no_labels_reports_zeros_and_says_so():
    """With nothing to find, recall is structurally zero rather than a result."""
    metrics = metrics_for(label_set(), [at(100), at(200)])

    assert metrics.ground_truth_moments == 0
    assert metrics.has_ground_truth is False
    assert metrics.recall == 0.0
    assert metrics.f1 == 0.0
    assert metrics.false_positives == 2


def test_labels_present_marks_the_run_as_measured():
    metrics = metrics_for(label_set(label(100)), [at(100)])

    assert metrics.has_ground_truth is True


def test_nothing_at_all_is_all_zeros():
    metrics = metrics_for(label_set(), [])

    assert (metrics.precision, metrics.recall, metrics.f1) == (0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    ("true_positives", "predicted", "expected"),
    [(0, 0, 0.0), (1, 1, 1.0), (1, 4, 0.25)],
)
def test_precision_rule(true_positives, predicted, expected):
    assert precision_of(true_positives, predicted) == expected


@pytest.mark.parametrize(
    ("true_positives", "ground_truth", "expected"),
    [(0, 0, 0.0), (0, 3, 0.0), (2, 4, 0.5)],
)
def test_recall_rule(true_positives, ground_truth, expected):
    assert recall_of(true_positives, ground_truth) == expected


def test_f1_of_two_zeros_is_zero_rather_than_undefined():
    assert f1_of(0.0, 0.0) == 0.0


# -- timing --------------------------------------------------------------------


def test_timing_error_is_reported_for_matched_moments():
    metrics = metrics_for(
        label_set(label(100), label(200), label(300)),
        [at(104), at(200), at(310)],
    )

    assert metrics.mean_absolute_timing_error_seconds == pytest.approx((4 + 0 + 10) / 3)
    assert metrics.median_absolute_timing_error_seconds == 4.0


def test_timing_error_is_none_when_nothing_matched():
    """Distinct from zero, which would read as 'matched perfectly'."""
    metrics = metrics_for(label_set(label(100)), [at(900)])

    assert metrics.mean_absolute_timing_error_seconds is None
    assert metrics.median_absolute_timing_error_seconds is None


def test_timing_error_ignores_direction():
    metrics = metrics_for(label_set(label(100), label(200)), [at(95), at(205)])

    assert metrics.mean_absolute_timing_error_seconds == 5.0


# -- rates ---------------------------------------------------------------------


def test_candidate_rate_is_per_hour():
    metrics = metrics_for(label_set(label(100)), [at(100), at(200), at(300)], duration=1800.0)

    assert metrics.candidate_rate_per_hour == 6.0


def test_the_crossing_rate_is_reported_separately():
    """An over-sensitive configuration shows up here before it shows up in F1."""
    metrics = metrics_for(label_set(label(100)), [at(100)], duration=3600.0, crossings=240)

    assert metrics.candidate_rate_per_hour == 1.0
    assert metrics.threshold_crossing_rate_per_hour == 240.0


def test_rates_are_none_over_a_zero_length_replay():
    metrics = metrics_for(label_set(), [], duration=0.0)

    assert metrics.candidate_rate_per_hour is None
    assert metrics.threshold_crossing_rate_per_hour is None


@pytest.mark.parametrize(
    ("count", "duration", "expected"),
    [(0, 3600.0, 0.0), (10, 3600.0, 10.0), (1, 60.0, 60.0), (5, 0.0, None), (5, -1.0, None)],
)
def test_rate_rule(count, duration, expected):
    assert rate_per_hour(count, duration) == expected


def test_no_metric_is_ever_nan():
    """The rule the whole module exists to keep."""
    for metrics in (
        metrics_for(label_set(), [], duration=0.0),
        metrics_for(label_set(label(100)), []),
        metrics_for(label_set(), [at(100)]),
    ):
        for value in (metrics.precision, metrics.recall, metrics.f1):
            assert value == value  # noqa: PLR0124  (a NaN would fail this)
