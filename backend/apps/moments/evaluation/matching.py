"""Pairing detector candidates with human labels.

The whole point is to stop one burst of activity flattering the numbers. A spike
that produces three candidates seconds apart has found *one* moment, not three,
so matching is strictly one-to-one: a candidate matches at most one label, and a
label at most one candidate. The extras are false positives, which is exactly
what they are.

**Algorithm.** Every (label, candidate) pair inside that label's tolerance is a
possible match. They are considered in order of how close they are and assigned
greedily, skipping any whose label or candidate is already taken.

**Tie-break.** Two pairs at the same absolute distance are ordered by earlier
label timestamp, then earlier candidate timestamp. Fully deterministic, so the
same inputs always give the same matching.

Greedy nearest is not guaranteed to be the globally optimal assignment. That is
deliberate: it is simple to explain, stable, and on ground truth spaced further
apart than its own tolerance — which is what an operator should be labelling —
it is optimal anyway.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from apps.moments.evaluation.labels import Label, LabelSet


@dataclass(frozen=True)
class Match:
    """One label and the candidate that answered it."""

    label: Label
    candidate: datetime

    @property
    def delta_seconds(self) -> float:
        """Signed: positive when the detector fired after the labelled instant."""
        return (self.candidate - self.label.timestamp).total_seconds()

    @property
    def absolute_delta_seconds(self) -> float:
        return abs(self.delta_seconds)


@dataclass(frozen=True)
class MatchResult:
    """The complete pairing, and everything left over on both sides."""

    matches: tuple[Match, ...]
    unmatched_labels: tuple[Label, ...]
    unmatched_candidates: tuple[datetime, ...]

    @property
    def true_positives(self) -> int:
        return len(self.matches)

    @property
    def false_positives(self) -> int:
        """Candidates nobody labelled — the detector firing at nothing."""
        return len(self.unmatched_candidates)

    @property
    def false_negatives(self) -> int:
        """Labelled moments the detector never found."""
        return len(self.unmatched_labels)


def match_candidates(
    labels: LabelSet | Iterable[Label],
    candidates: Sequence[datetime],
) -> MatchResult:
    """Pair candidates with labels, one to one."""
    ordered_labels = sorted(labels, key=lambda label: label.timestamp)
    ordered_candidates = sorted(candidates)

    pairs = [
        (
            abs((candidate - label.timestamp).total_seconds()),
            label_index,
            candidate_index,
        )
        for label_index, label in enumerate(ordered_labels)
        for candidate_index, candidate in enumerate(ordered_candidates)
        if label.accepts(candidate)
    ]
    # Closest first; ties resolved by the earlier label, then the earlier
    # candidate. Both index sequences are already in time order, so sorting on
    # them is sorting on time.
    pairs.sort()

    taken_labels: set[int] = set()
    taken_candidates: set[int] = set()
    matches: list[Match] = []

    for _distance, label_index, candidate_index in pairs:
        if label_index in taken_labels or candidate_index in taken_candidates:
            continue
        taken_labels.add(label_index)
        taken_candidates.add(candidate_index)
        matches.append(
            Match(label=ordered_labels[label_index], candidate=ordered_candidates[candidate_index])
        )

    matches.sort(key=lambda match: match.label.timestamp)

    return MatchResult(
        matches=tuple(matches),
        unmatched_labels=tuple(
            label for index, label in enumerate(ordered_labels) if index not in taken_labels
        ),
        unmatched_candidates=tuple(
            candidate
            for index, candidate in enumerate(ordered_candidates)
            if index not in taken_candidates
        ),
    )
