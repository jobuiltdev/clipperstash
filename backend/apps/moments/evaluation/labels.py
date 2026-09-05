"""Operator-created ground truth: which moments a human thought were worth clipping.

A label is a timestamp and a tolerance, nothing more. It deliberately requires
no chat text, no chatter identity and no Twitch clip — the point is to record a
person's judgement about *when* something happened, so the detector's answer can
be compared against it.

These files are local evaluation artifacts. Real ones are never committed; see
`backend/evaluation_data/README.md`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from django.utils.dateparse import parse_datetime

from apps.moments.evaluation.errors import LabelFileError

SCHEMA_VERSION = 1
DEFAULT_TOLERANCE_SECONDS = 10.0

_TOP_LEVEL_FIELDS = frozenset({"version", "session_id", "default_tolerance_seconds", "moments"})
_MOMENT_FIELDS = frozenset({"timestamp", "tolerance_seconds", "note"})


@dataclass(frozen=True)
class Label:
    """One moment a human judged clip-worthy.

    `tolerance_seconds` is how far from this instant a detector candidate may
    land and still be the same event. A person watching a stream cannot note a
    timestamp to the second, and the detector's window ends at its own
    evaluation tick, so an exact match would be meaningless.
    """

    timestamp: datetime
    tolerance_seconds: float
    note: str = ""

    @property
    def window_start(self) -> datetime:
        return self.timestamp - timedelta(seconds=self.tolerance_seconds)

    @property
    def window_end(self) -> datetime:
        return self.timestamp + timedelta(seconds=self.tolerance_seconds)

    def accepts(self, moment: datetime) -> bool:
        return self.window_start <= moment <= self.window_end


@dataclass(frozen=True)
class LabelSet:
    """Every label for one session, in time order."""

    session_id: int
    default_tolerance_seconds: float
    labels: tuple[Label, ...]

    def __len__(self) -> int:
        return len(self.labels)

    def __iter__(self):
        return iter(self.labels)

    @property
    def overlapping_pairs(self) -> tuple[tuple[Label, Label], ...]:
        """Adjacent labels whose tolerance windows touch.

        Not an error — two real events can genuinely fall close together — but
        matching becomes ambiguous in the overlap, so the caller is given the
        chance to say so.
        """
        overlapping = [
            (first, second)
            for first, second in zip(self.labels, self.labels[1:], strict=False)
            if second.window_start <= first.window_end
        ]
        return tuple(overlapping)


def load_labels(path: str | Path, *, session_id: int | None = None) -> LabelSet:
    """Read and validate one label file."""
    location = Path(path)
    try:
        raw = location.read_text(encoding="utf-8")
    except OSError as exc:
        raise LabelFileError(f"Could not read the label file at {location}.") from exc

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LabelFileError(
            f"The label file at {location} is not valid JSON: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})."
        ) from exc

    return labels_from_document(document, session_id=session_id, source=str(location))


def labels_from_document(
    document: Any,
    *,
    session_id: int | None = None,
    source: str = "<labels>",
) -> LabelSet:
    """Validate one already-parsed label document.

    `session_id`, when given, is the session actually being evaluated. A label
    file naming a different session is refused: scoring one session's chat
    against another's ground truth would produce metrics that look real.
    """
    if not isinstance(document, dict):
        raise LabelFileError(f"{source}: a label file must be a JSON object.")

    unknown = sorted(set(document) - _TOP_LEVEL_FIELDS)
    if unknown:
        raise LabelFileError(
            f"{source}: unknown field(s) {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(_TOP_LEVEL_FIELDS))}."
        )

    version = document.get("version")
    if version != SCHEMA_VERSION:
        raise LabelFileError(
            f"{source}: unsupported label schema version {version!r}; expected {SCHEMA_VERSION}."
        )

    file_session_id = document.get("session_id")
    if not isinstance(file_session_id, int) or isinstance(file_session_id, bool):
        raise LabelFileError(f"{source}: 'session_id' is required and must be an integer.")
    if session_id is not None and file_session_id != session_id:
        raise LabelFileError(
            f"{source}: these labels are for session {file_session_id}, "
            f"but session {session_id} is being evaluated."
        )

    default_tolerance = _read_tolerance(
        document.get("default_tolerance_seconds", DEFAULT_TOLERANCE_SECONDS),
        where=f"{source}: default_tolerance_seconds",
    )

    entries = document.get("moments")
    if not isinstance(entries, list):
        raise LabelFileError(f"{source}: 'moments' is required and must be a JSON array.")

    labels = [
        _read_label(entry, index=index, default_tolerance=default_tolerance, source=source)
        for index, entry in enumerate(entries)
    ]
    labels.sort(key=lambda label: label.timestamp)

    _reject_duplicates(labels, source=source)

    return LabelSet(
        session_id=file_session_id,
        default_tolerance_seconds=default_tolerance,
        labels=tuple(labels),
    )


def _read_label(entry: Any, *, index: int, default_tolerance: float, source: str) -> Label:
    where = f"{source}: moments[{index}]"

    if not isinstance(entry, dict):
        raise LabelFileError(f"{where} must be a JSON object.")

    unknown = sorted(set(entry) - _MOMENT_FIELDS)
    if unknown:
        raise LabelFileError(
            f"{where}: unknown field(s) {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(_MOMENT_FIELDS))}."
        )

    raw_timestamp = entry.get("timestamp")
    if not isinstance(raw_timestamp, str):
        raise LabelFileError(f"{where}: 'timestamp' is required and must be a string.")

    try:
        parsed = parse_datetime(raw_timestamp)
    except ValueError:
        parsed = None
    if parsed is None:
        raise LabelFileError(f"{where}: could not read {raw_timestamp!r} as an ISO 8601 timestamp.")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LabelFileError(
            f"{where}: 'timestamp' must carry a timezone, e.g. 2026-09-05T18:30:15Z. "
            "A naive instant would silently shift every match."
        )

    tolerance = (
        default_tolerance
        if "tolerance_seconds" not in entry
        else _read_tolerance(entry["tolerance_seconds"], where=f"{where}.tolerance_seconds")
    )

    note = entry.get("note", "")
    if not isinstance(note, str):
        raise LabelFileError(f"{where}: 'note' must be a string when present.")

    return Label(timestamp=parsed, tolerance_seconds=tolerance, note=note.strip())


def _read_tolerance(value: Any, *, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise LabelFileError(f"{where} must be a number, got {value!r}.")
    if value <= 0:
        raise LabelFileError(
            f"{where} must be greater than zero, got {value!r}. A zero window would "
            "require the detector to land on the exact second a person wrote down."
        )
    return float(value)


def _reject_duplicates(labels: list[Label], *, source: str) -> None:
    """Two labels at the same instant are a mistake, not two events.

    They would compete for the same candidate and one would always be recorded
    as a miss, quietly depressing recall.
    """
    for first, second in zip(labels, labels[1:], strict=False):
        if first.timestamp == second.timestamp:
            raise LabelFileError(
                f"{source}: two labels share the timestamp {first.timestamp.isoformat()}. "
                "One event cannot be matched twice, so the duplicate would always "
                "count as a miss."
            )
