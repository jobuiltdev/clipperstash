"""The machine-readable evaluation report.

Everything here is aggregate. There is no chat text, no chatter hash, no raw
Twitch id and no provider payload anywhere in the structure — a report is safe to
keep on disk, diff between runs and paste into a note, which is the whole reason
it exists. `test_report.py` asserts that by construction rather than by
inspection.

Reports are still local artifacts. They describe a real stream's activity in
detail, so they belong in the ignored evaluation directory and are never
committed.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from apps.moments.evaluation.comparison import ConfigurationResult
from apps.moments.evaluation.labels import LabelSet
from apps.moments.evaluation.metrics import Metrics
from apps.moments.evaluation.replay import ReplayResult

SCHEMA_VERSION = 1


def build_report(
    *,
    session_id: int,
    labels: LabelSet,
    results: Sequence[ConfigurationResult],
    cadence_seconds: float,
    generated_at: datetime,
) -> dict[str, Any]:
    """Assemble one run's findings as plain JSON-ready data."""
    first = results[0].replay if results else None

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "session": {
            "id": session_id,
            "message_count": first.sample_count if first else 0,
            "first_evaluated_at": _iso(first.first_tick) if first else None,
            "last_evaluated_at": _iso(first.last_tick) if first else None,
            "duration_seconds": first.duration_seconds if first else 0.0,
        },
        "cadence_seconds": cadence_seconds,
        "labels": {
            "count": len(labels),
            "default_tolerance_seconds": labels.default_tolerance_seconds,
            "moments": [
                {
                    "timestamp": _iso(label.timestamp),
                    "tolerance_seconds": label.tolerance_seconds,
                }
                for label in labels
            ],
        },
        "results": [_result(result) for result in results],
    }


def write_report(report: dict[str, Any], path: str | Path) -> Path:
    """Write a report to a local path, creating parent directories."""
    location = Path(path)
    location.parent.mkdir(parents=True, exist_ok=True)
    location.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return location


# -- internals ----------------------------------------------------------------


def _result(result: ConfigurationResult) -> dict[str, Any]:
    return {
        "configuration": result.configuration.as_dict(),
        "observations": _observations(result.replay),
        "metrics": _metrics(result.metrics),
        "candidate_timestamps": [_iso(moment) for moment in result.replay.candidate_timestamps],
        "matches": [
            {
                "label_timestamp": _iso(match.label.timestamp),
                "candidate_timestamp": _iso(match.candidate),
                "delta_seconds": match.delta_seconds,
            }
            for match in result.match_result.matches
        ],
        "unmatched_label_timestamps": [
            _iso(label.timestamp) for label in result.match_result.unmatched_labels
        ],
        "unmatched_candidate_timestamps": [
            _iso(moment) for moment in result.match_result.unmatched_candidates
        ],
    }


def _observations(replay: ReplayResult) -> dict[str, Any]:
    """Aggregates over every tick.

    Deliberately a summary rather than the full series: a one-second cadence
    over a three-hour session is ten thousand ticks, and a report nobody opens
    is not evidence.
    """
    scores = [observation.total_score for observation in replay.observations]
    return {
        "count": len(replay.observations),
        "gate_passed": sum(1 for obs in replay.observations if obs.gate_passed),
        "threshold_crossings": len(replay.threshold_crossings),
        "candidates": len(replay.candidates),
        "cooldown_suppressed": len(replay.suppressed),
        "max_total_score": max(scores) if scores else None,
        "mean_total_score": round(sum(scores) / len(scores), 4) if scores else None,
    }


def _metrics(metrics: Metrics) -> dict[str, Any]:
    return {
        "ground_truth_moments": metrics.ground_truth_moments,
        "predicted_moments": metrics.predicted_moments,
        "true_positives": metrics.true_positives,
        "false_positives": metrics.false_positives,
        "false_negatives": metrics.false_negatives,
        "precision": round(metrics.precision, 4),
        "recall": round(metrics.recall, 4),
        "f1": round(metrics.f1, 4),
        "has_ground_truth": metrics.has_ground_truth,
        "mean_absolute_timing_error_seconds": _rounded(metrics.mean_absolute_timing_error_seconds),
        "median_absolute_timing_error_seconds": _rounded(
            metrics.median_absolute_timing_error_seconds
        ),
        "candidate_rate_per_hour": _rounded(metrics.candidate_rate_per_hour),
        "threshold_crossing_rate_per_hour": _rounded(metrics.threshold_crossing_rate_per_hour),
        "duration_seconds": metrics.duration_seconds,
    }


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()
