"""The detector's single pure entry point.

`score_samples()` takes chat samples and an explicit evaluation time and returns
a `MomentScore`. It performs no I/O, reads no clock and knows nothing about the
database — the same inputs always give the same answer, which is what makes
historical replay and deterministic tests possible.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from apps.moments.detector.config import DEFAULT_CONFIG, DetectorConfig
from apps.moments.detector.scorer import (
    RATIO_PRECISION,
    ComponentScores,
    component_scores,
    total_score,
    velocity_ratio,
)
from apps.moments.detector.signals import WindowSignals, extract_signals
from apps.moments.detector.window import ChatSample, WindowBounds, build_windows, split_samples


@dataclass(frozen=True)
class MomentScore:
    """One evaluation's full working, not just its verdict.

    Every raw count and component score is carried alongside the total, so a
    stored candidate explains itself and a rejected window can still be
    inspected during calibration. Deliberately knows nothing about
    `MomentCandidate` or any database id.
    """

    bounds: WindowBounds
    current: WindowSignals
    baseline: WindowSignals
    velocity_ratio: float
    components: ComponentScores
    total: float
    activity_gate_passed: bool
    qualifies: bool
    threshold: float

    @property
    def evaluation_time(self) -> datetime:
        return self.bounds.evaluation_time


def passes_activity_gate(
    current: WindowSignals,
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> bool:
    """Whether the window is busy enough to draw any conclusion from.

    A handful of messages from one or two people can produce a high ratio
    without meaning anything, so quiet windows are excluded before the score is
    consulted at all.
    """
    return (
        current.message_count >= config.min_current_messages
        and current.unique_chatters >= config.min_current_unique_chatters
    )


def score_samples(
    samples: Iterable[ChatSample],
    evaluation_time: datetime,
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> MomentScore:
    """Score the chat around `evaluation_time`.

    Samples outside both windows are ignored, so a caller may pass a slightly
    wider slice than needed. A window is always scored, even when the activity
    gate fails: the diagnostics are useful for calibration, and only
    `qualifies` reflects the gate.
    """
    bounds = build_windows(evaluation_time, config=config)
    windowed = split_samples(samples, bounds)

    current = extract_signals(
        windowed.current, duration_seconds=bounds.current_seconds, config=config
    )
    baseline = extract_signals(
        windowed.baseline, duration_seconds=bounds.baseline_seconds, config=config
    )

    components = component_scores(current, baseline, config=config)
    total = total_score(components, config=config)
    gate_passed = passes_activity_gate(current, config=config)

    return MomentScore(
        bounds=bounds,
        current=current,
        baseline=baseline,
        velocity_ratio=round(velocity_ratio(current, baseline, config=config), RATIO_PRECISION),
        components=components,
        total=total,
        activity_gate_passed=gate_passed,
        # Both conditions are required: a quiet window never qualifies however
        # it scores, and a busy window still has to clear the threshold.
        qualifies=gate_passed and total >= config.candidate_threshold,
        threshold=config.candidate_threshold,
    )
