"""Chat samples and the two windows the detector compares.

Pure: no Django, no ORM, no clock. The evaluation time is always supplied by the
caller, which is what makes historical replay and deterministic tests possible.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.moments.detector.config import DEFAULT_CONFIG, DetectorConfig


class DetectorInputError(ValueError):
    """A sample or evaluation time the detector cannot work with."""


@dataclass(frozen=True)
class ChatSample:
    """One chat message, reduced to what scoring reads.

    Deliberately not a `ChatMessage`: the scoring layer never sees a model
    instance, a queryset or any field it has no use for. `chatter_hash` is the
    pseudonymous identity persisted in Milestone 4 — the raw Twitch user id does
    not exist anywhere in this pipeline.
    """

    timestamp: datetime
    chatter_hash: str
    text: str
    emote_count: int = 0


@dataclass(frozen=True)
class WindowBounds:
    """The two comparison windows for one evaluation.

    Both are half-open as `(start, end]`, and they abut exactly:
    `baseline_end == current_start`. A message landing precisely on that
    instant therefore belongs to the baseline and to nothing else, so no message
    is ever counted twice or dropped between them.

        baseline: (T - 70s, T - 10s]
        current:  (T - 10s, T]
    """

    evaluation_time: datetime
    current_start: datetime
    current_end: datetime
    baseline_start: datetime
    baseline_end: datetime

    @property
    def current_seconds(self) -> float:
        return (self.current_end - self.current_start).total_seconds()

    @property
    def baseline_seconds(self) -> float:
        return (self.baseline_end - self.baseline_start).total_seconds()

    def contains_current(self, moment: datetime) -> bool:
        return self.current_start < moment <= self.current_end

    def contains_baseline(self, moment: datetime) -> bool:
        return self.baseline_start < moment <= self.baseline_end


@dataclass(frozen=True)
class WindowedSamples:
    """Samples split into the two windows, in timestamp order."""

    bounds: WindowBounds
    current: tuple[ChatSample, ...]
    baseline: tuple[ChatSample, ...]


def build_windows(
    evaluation_time: datetime,
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> WindowBounds:
    """Derive both window boundaries from one evaluation instant."""
    require_aware(evaluation_time, "evaluation time")

    current_start = evaluation_time - timedelta(seconds=config.current_window_seconds)
    baseline_start = current_start - timedelta(seconds=config.baseline_window_seconds)

    return WindowBounds(
        evaluation_time=evaluation_time,
        current_start=current_start,
        current_end=evaluation_time,
        baseline_start=baseline_start,
        baseline_end=current_start,
    )


def split_samples(
    samples: Iterable[ChatSample],
    bounds: WindowBounds,
) -> WindowedSamples:
    """Sort samples into the current and baseline windows.

    Anything outside both windows is ignored rather than rejected: the caller
    may hand over a slightly wider query result, and older history is simply not
    this evaluation's concern.
    """
    current: list[ChatSample] = []
    baseline: list[ChatSample] = []

    for sample in samples:
        require_aware(sample.timestamp, "chat sample timestamp")
        if bounds.contains_current(sample.timestamp):
            current.append(sample)
        elif bounds.contains_baseline(sample.timestamp):
            baseline.append(sample)

    current.sort(key=lambda sample: sample.timestamp)
    baseline.sort(key=lambda sample: sample.timestamp)

    return WindowedSamples(bounds=bounds, current=tuple(current), baseline=tuple(baseline))


def require_aware(moment: datetime, label: str) -> None:
    """Refuse naive datetimes outright.

    A naive timestamp here would silently shift every window, so it is an error
    rather than something to guess at.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise DetectorInputError(f"The {label} must be timezone-aware.")
