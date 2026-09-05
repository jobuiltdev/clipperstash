"""Ground-truth label files.

Labels are the only part of an evaluation a person writes by hand, so this is
where a typo turns into a wrong conclusion. Everything is validated and nothing
is guessed: a mistyped field, a naive timestamp or labels for the wrong session
are refused rather than quietly evaluated into a plausible-looking number.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.moments.evaluation.errors import LabelFileError
from apps.moments.evaluation.labels import DEFAULT_TOLERANCE_SECONDS, load_labels

from .conftest import at

VALID = {
    "version": 1,
    "session_id": 7,
    "moments": [
        {"timestamp": "2026-01-01T12:02:00Z", "note": "big play"},
        {"timestamp": "2026-01-01T12:05:00Z", "tolerance_seconds": 25},
    ],
}


def write(tmp_path, document, name: str = "labels.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


# -- valid files ---------------------------------------------------------------


def test_a_valid_file_loads(tmp_path):
    labels = load_labels(write(tmp_path, VALID))

    assert labels.session_id == 7
    assert len(labels) == 2
    assert labels.labels[0].timestamp == at(120)
    assert labels.labels[0].note == "big play"


def test_the_note_is_optional(tmp_path):
    labels = load_labels(
        write(tmp_path, {**VALID, "moments": [{"timestamp": "2026-01-01T12:00:00Z"}]})
    )

    assert labels.labels[0].note == ""


def test_a_moment_without_a_tolerance_inherits_the_session_default(tmp_path):
    labels = load_labels(write(tmp_path, VALID))

    assert labels.default_tolerance_seconds == DEFAULT_TOLERANCE_SECONDS
    assert labels.labels[0].tolerance_seconds == DEFAULT_TOLERANCE_SECONDS


def test_a_moment_may_override_the_default_tolerance(tmp_path):
    labels = load_labels(write(tmp_path, VALID))

    assert labels.labels[1].tolerance_seconds == 25.0


def test_a_session_default_applies_to_every_unspecified_moment(tmp_path):
    document = {
        "version": 1,
        "session_id": 7,
        "default_tolerance_seconds": 30,
        "moments": [
            {"timestamp": "2026-01-01T12:00:00Z"},
            {"timestamp": "2026-01-01T12:10:00Z", "tolerance_seconds": 5},
        ],
    }

    labels = load_labels(write(tmp_path, document))

    assert [label.tolerance_seconds for label in labels] == [30.0, 5.0]


def test_labels_are_sorted_by_time(tmp_path):
    document = {
        "version": 1,
        "session_id": 7,
        "moments": [
            {"timestamp": "2026-01-01T12:10:00Z"},
            {"timestamp": "2026-01-01T12:00:00Z"},
        ],
    }

    labels = load_labels(write(tmp_path, document))

    assert [label.timestamp for label in labels] == [at(0), at(600)]


def test_an_empty_label_list_is_allowed(tmp_path):
    """A session with nothing worth clipping is a legitimate observation."""
    labels = load_labels(write(tmp_path, {**VALID, "moments": []}))

    assert len(labels) == 0


def test_a_labels_window_spans_its_tolerance(tmp_path):
    label = load_labels(write(tmp_path, VALID)).labels[0]

    assert label.accepts(at(120))
    assert label.accepts(at(120 + DEFAULT_TOLERANCE_SECONDS))
    assert label.accepts(at(120 - DEFAULT_TOLERANCE_SECONDS))
    assert not label.accepts(at(120 + DEFAULT_TOLERANCE_SECONDS + 1))


# -- session identity ----------------------------------------------------------


def test_labels_for_another_session_are_refused(tmp_path):
    """Otherwise one session's chat would be scored against another's ground truth."""
    with pytest.raises(LabelFileError, match="labels are for session 7"):
        load_labels(write(tmp_path, VALID), session_id=8)


def test_labels_for_the_right_session_are_accepted(tmp_path):
    assert load_labels(write(tmp_path, VALID), session_id=7).session_id == 7


def test_a_missing_session_id_is_refused(tmp_path):
    document = {"version": 1, "moments": []}

    with pytest.raises(LabelFileError, match="'session_id' is required"):
        load_labels(write(tmp_path, document))


def test_a_non_integer_session_id_is_refused(tmp_path):
    with pytest.raises(LabelFileError, match="'session_id' is required"):
        load_labels(write(tmp_path, {**VALID, "session_id": "7"}))


# -- schema version ------------------------------------------------------------


def test_a_missing_version_is_refused(tmp_path):
    document = {"session_id": 7, "moments": []}

    with pytest.raises(LabelFileError, match="unsupported label schema version"):
        load_labels(write(tmp_path, document))


def test_a_future_version_is_refused(tmp_path):
    with pytest.raises(LabelFileError, match="unsupported label schema version 2"):
        load_labels(write(tmp_path, {**VALID, "version": 2}))


# -- malformed input -----------------------------------------------------------


def test_an_unknown_top_level_field_is_refused(tmp_path):
    with pytest.raises(LabelFileError, match="unknown field"):
        load_labels(write(tmp_path, {**VALID, "streamer": "someone"}))


def test_an_unknown_moment_field_is_refused(tmp_path):
    document = {**VALID, "moments": [{"timestamp": "2026-01-01T12:00:00Z", "clip_id": "abc"}]}

    with pytest.raises(LabelFileError, match="unknown field"):
        load_labels(write(tmp_path, document))


def test_a_naive_timestamp_is_refused(tmp_path):
    """A missing timezone would silently shift every match by the host's offset."""
    document = {**VALID, "moments": [{"timestamp": "2026-01-01T12:00:00"}]}

    with pytest.raises(LabelFileError, match="must carry a timezone"):
        load_labels(write(tmp_path, document))


def test_an_unreadable_timestamp_is_refused(tmp_path):
    document = {**VALID, "moments": [{"timestamp": "yesterday evening"}]}

    with pytest.raises(LabelFileError, match="ISO 8601"):
        load_labels(write(tmp_path, document))


def test_a_missing_timestamp_is_refused(tmp_path):
    document = {**VALID, "moments": [{"note": "no time given"}]}

    with pytest.raises(LabelFileError, match="'timestamp' is required"):
        load_labels(write(tmp_path, document))


@pytest.mark.parametrize("tolerance", [0, -5, "ten", True])
def test_an_invalid_tolerance_is_refused(tmp_path, tolerance):
    document = {
        **VALID,
        "moments": [{"timestamp": "2026-01-01T12:00:00Z", "tolerance_seconds": tolerance}],
    }

    with pytest.raises(LabelFileError):
        load_labels(write(tmp_path, document))


def test_an_invalid_default_tolerance_is_refused(tmp_path):
    with pytest.raises(LabelFileError, match="greater than zero"):
        load_labels(write(tmp_path, {**VALID, "default_tolerance_seconds": 0}))


def test_a_non_string_note_is_refused(tmp_path):
    document = {**VALID, "moments": [{"timestamp": "2026-01-01T12:00:00Z", "note": 12}]}

    with pytest.raises(LabelFileError, match="'note' must be a string"):
        load_labels(write(tmp_path, document))


def test_moments_must_be_a_list(tmp_path):
    with pytest.raises(LabelFileError, match="must be a JSON array"):
        load_labels(write(tmp_path, {**VALID, "moments": {}}))


def test_a_moment_must_be_an_object(tmp_path):
    with pytest.raises(LabelFileError, match="must be a JSON object"):
        load_labels(write(tmp_path, {**VALID, "moments": ["2026-01-01T12:00:00Z"]}))


def test_malformed_json_is_reported_with_its_position(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"version": 1,,}', encoding="utf-8")

    with pytest.raises(LabelFileError, match="not valid JSON"):
        load_labels(str(path))


def test_a_missing_file_is_reported_clearly(tmp_path):
    with pytest.raises(LabelFileError, match="Could not read"):
        load_labels(str(tmp_path / "absent.json"))


# -- duplicates and overlaps ---------------------------------------------------


def test_two_labels_at_the_same_instant_are_refused(tmp_path):
    """One event cannot be matched twice, so the duplicate would always miss."""
    document = {
        **VALID,
        "moments": [
            {"timestamp": "2026-01-01T12:00:00Z"},
            {"timestamp": "2026-01-01T12:00:00Z", "note": "same moment again"},
        ],
    }

    with pytest.raises(LabelFileError, match="share the timestamp"):
        load_labels(write(tmp_path, document))


def test_overlapping_tolerance_windows_are_reported_but_allowed(tmp_path):
    """Two real events can genuinely fall close together; the caller is told."""
    document = {
        "version": 1,
        "session_id": 7,
        "default_tolerance_seconds": 10,
        "moments": [
            {"timestamp": "2026-01-01T12:00:00Z"},
            {"timestamp": "2026-01-01T12:00:15Z"},
        ],
    }

    labels = load_labels(write(tmp_path, document))

    assert len(labels) == 2
    assert len(labels.overlapping_pairs) == 1


def test_well_separated_labels_report_no_overlap(tmp_path):
    assert load_labels(write(tmp_path, VALID)).overlapping_pairs == ()


# -- the tracked example -------------------------------------------------------


def test_the_tracked_example_file_is_valid():
    """The example an operator copies must actually load."""
    example = (
        Path(__file__).resolve().parents[4] / "evaluation_data" / "examples" / "labels.example.json"
    )

    labels = load_labels(example)

    assert len(labels) == 3
    assert labels.default_tolerance_seconds == 10.0


def test_the_tracked_example_carries_no_chat_or_identity():
    """A tracked fixture must never contain real material."""
    example = (
        Path(__file__).resolve().parents[4] / "evaluation_data" / "examples" / "labels.example.json"
    )
    document = json.loads(example.read_text(encoding="utf-8"))

    assert set(document) <= {"version", "session_id", "default_tolerance_seconds", "moments"}
    for moment in document["moments"]:
        assert set(moment) <= {"timestamp", "tolerance_seconds", "note"}
        assert "synthetic" in moment.get("note", "synthetic")
