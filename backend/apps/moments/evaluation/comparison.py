"""Running several calibrations over the same chat and the same ground truth.

Comparison is the only honest way to judge a detector setting: a number on its
own says nothing, and a number next to the baseline's says a great deal.

Results are **ranked, not chosen.** `rank()` sorts them so a table is readable,
and nothing anywhere promotes the top row to production. A configuration wins on
one session's handful of labels for all sorts of uninteresting reasons, and
turning that into a default without a person looking at several sessions is
exactly the mistake this tooling exists to prevent.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from apps.moments.detector import ChatSample
from apps.moments.evaluation.config import NamedConfig
from apps.moments.evaluation.labels import LabelSet
from apps.moments.evaluation.matching import MatchResult, match_candidates
from apps.moments.evaluation.metrics import Metrics, compute_metrics
from apps.moments.evaluation.replay import DEFAULT_CADENCE_SECONDS, ReplayResult, replay_samples


@dataclass(frozen=True)
class ConfigurationResult:
    """One calibration's full result over one session."""

    configuration: NamedConfig
    replay: ReplayResult
    match_result: MatchResult
    metrics: Metrics

    @property
    def name(self) -> str:
        return self.configuration.name


def evaluate_configuration(
    samples: Sequence[ChatSample],
    labels: LabelSet,
    configuration: NamedConfig,
    *,
    cadence_seconds: float = DEFAULT_CADENCE_SECONDS,
) -> ConfigurationResult:
    """Replay, match and measure one calibration. Writes nothing."""
    replay = replay_samples(samples, config=configuration.config, cadence_seconds=cadence_seconds)
    candidates = replay.candidate_timestamps
    match_result = match_candidates(labels, candidates)

    metrics = compute_metrics(
        match_result,
        ground_truth_moments=len(labels),
        predicted_moments=len(candidates),
        threshold_crossings=len(replay.threshold_crossings),
        observations=len(replay.observations),
        duration_seconds=replay.duration_seconds,
    )

    return ConfigurationResult(
        configuration=configuration,
        replay=replay,
        match_result=match_result,
        metrics=metrics,
    )


def compare_configurations(
    samples: Sequence[ChatSample],
    labels: LabelSet,
    configurations: Sequence[NamedConfig],
    *,
    cadence_seconds: float = DEFAULT_CADENCE_SECONDS,
) -> list[ConfigurationResult]:
    """Evaluate every calibration over the same input, in the order given."""
    return [
        evaluate_configuration(samples, labels, configuration, cadence_seconds=cadence_seconds)
        for configuration in configurations
    ]


def rank(results: Sequence[ConfigurationResult]) -> list[ConfigurationResult]:
    """Order results for reading: best F1 first, name as a deterministic tie-break.

    Ordering only. Nothing downstream treats position zero as a winner, and no
    caller may: promoting a default is a reviewed decision backed by several
    sessions, not the output of a sort.
    """
    return sorted(results, key=lambda result: (-result.metrics.f1, result.name))
