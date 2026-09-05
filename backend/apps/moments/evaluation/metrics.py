"""Turning a matching into numbers a person can act on.

Two decisions run through this module.

**Nothing is ever NaN.** Every ratio has an explicit rule for an empty
denominator, because a metrics table that prints `nan` teaches an operator to
ignore it. The rules are stated on each function and repeated in the README.

**Precision and recall are not enough on their own.** On a handful of labels
they are easy to flatter — a configuration that fires constantly can score well
on recall while being useless in production. So the candidate rate per hour and
the threshold-crossing rate per hour are reported alongside, because an
over-sensitive calibration shows up there immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean, median

from apps.moments.evaluation.matching import MatchResult

SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True)
class Metrics:
    """One configuration's quality against one session's ground truth."""

    ground_truth_moments: int
    predicted_moments: int
    threshold_crossings: int
    observations: int

    true_positives: int
    false_positives: int
    false_negatives: int

    precision: float
    recall: float
    f1: float

    # None rather than zero when nothing matched: "no matches" and "matched
    # perfectly, zero error" are different findings and must not print alike.
    mean_absolute_timing_error_seconds: float | None
    median_absolute_timing_error_seconds: float | None

    # None when the replay covered no time at all, for the same reason.
    candidate_rate_per_hour: float | None
    threshold_crossing_rate_per_hour: float | None

    duration_seconds: float

    @property
    def has_ground_truth(self) -> bool:
        """Whether recall means anything at all for this run.

        With no labels there is nothing to recall, so recall and F1 are reported
        as 0.0 and this flag is what tells a reader those zeros are structural
        rather than a result.
        """
        return self.ground_truth_moments > 0


def precision_of(true_positives: int, predicted: int) -> float:
    """Share of candidates that were real. Zero when nothing was predicted.

    A detector that never fires has not achieved perfect precision; it has
    achieved nothing, so the vacuous 1.0 is deliberately not used.
    """
    if predicted == 0:
        return 0.0
    return true_positives / predicted


def recall_of(true_positives: int, ground_truth: int) -> float:
    """Share of labelled moments that were found.

    Zero when there are labels and none were matched, and also zero when there
    are no labels at all — with nothing to find, there is no recall to report.
    `Metrics.has_ground_truth` distinguishes the two.
    """
    if ground_truth == 0:
        return 0.0
    return true_positives / ground_truth


def f1_of(precision: float, recall: float) -> float:
    """Harmonic mean, or zero when both are zero."""
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def rate_per_hour(count: int, duration_seconds: float) -> float | None:
    """Events per hour, or None over a zero-length replay."""
    if duration_seconds <= 0:
        return None
    return count * SECONDS_PER_HOUR / duration_seconds


def compute_metrics(
    match_result: MatchResult,
    *,
    ground_truth_moments: int,
    predicted_moments: int,
    threshold_crossings: int,
    observations: int,
    duration_seconds: float,
) -> Metrics:
    """Assemble every metric from one matching."""
    true_positives = match_result.true_positives
    precision = precision_of(true_positives, predicted_moments)
    recall = recall_of(true_positives, ground_truth_moments)

    errors = [match.absolute_delta_seconds for match in match_result.matches]

    return Metrics(
        ground_truth_moments=ground_truth_moments,
        predicted_moments=predicted_moments,
        threshold_crossings=threshold_crossings,
        observations=observations,
        true_positives=true_positives,
        false_positives=match_result.false_positives,
        false_negatives=match_result.false_negatives,
        precision=precision,
        recall=recall,
        f1=f1_of(precision, recall),
        mean_absolute_timing_error_seconds=fmean(errors) if errors else None,
        median_absolute_timing_error_seconds=median(errors) if errors else None,
        candidate_rate_per_hour=rate_per_hour(predicted_moments, duration_seconds),
        threshold_crossing_rate_per_hour=rate_per_hour(threshold_crossings, duration_seconds),
        duration_seconds=duration_seconds,
    )
