"""The JSON report, and the promise that it is safe to keep.

A report describes a real broadcast in detail, so the guarantee that matters is
that it contains nothing about the people who were watching. These tests walk
the entire structure rather than checking a field list, so a value added later
in a nested position is still caught.
"""

from __future__ import annotations

import json

from django.utils import timezone

from apps.moments.evaluation import baseline, comparison, report
from apps.moments.evaluation.labels import Label, LabelSet

from .conftest import PLAIN_TEXT, REACTION_TEXT, at, calm_baseline, hype_burst, ordered

# Strings that must never survive into a report. The first two are the fixture
# chat itself; the rest are field names that would mean identity leaked.
FORBIDDEN_VALUES = (PLAIN_TEXT, REACTION_TEXT, "viewer-", "hype-")
FORBIDDEN_KEYS = (
    "text",
    "chatter",
    "chatter_hash",
    "chatter_user_id_hash",
    "user_id",
    "twitch_message_id",
    "twitch_event_message_id",
    "access_token",
    "refresh_token",
    "client_secret",
    "payload",
    "edit_url",
)


def build(samples, labels):
    results = comparison.compare_configurations(samples, labels, [baseline()], cadence_seconds=5.0)
    return report.build_report(
        session_id=42,
        labels=labels,
        results=results,
        cadence_seconds=5.0,
        generated_at=timezone.now(),
    )


def label_set(*seconds: float) -> LabelSet:
    return LabelSet(
        session_id=42,
        default_tolerance_seconds=10.0,
        labels=tuple(Label(timestamp=at(value), tolerance_seconds=10.0) for value in seconds),
    )


def walk(node):
    """Every key and scalar in the document, however deeply nested."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, None
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)
    else:
        yield None, node


def sample_run():
    return ordered(
        calm_baseline(start=0.0, end=120.0),
        hype_burst(start=120.0, end=130.0),
        calm_baseline(start=130.0, end=200.0),
    )


# -- shape ---------------------------------------------------------------------


def test_a_report_describes_the_session_and_the_grid():
    document = build(sample_run(), label_set(122))

    assert document["schema_version"] == 1
    assert document["session"]["id"] == 42
    assert document["session"]["message_count"] > 0
    assert document["cadence_seconds"] == 5.0


def test_a_report_records_the_configuration_it_used():
    document = build(sample_run(), label_set(122))
    configuration = document["results"][0]["configuration"]

    assert configuration["name"] == "baseline"
    assert configuration["candidate_threshold"] == 70.0
    assert set(configuration["weights"]) == {
        "velocity",
        "reaction",
        "diversity",
        "emote",
        "absolute_activity",
    }


def test_a_report_summarizes_observations_rather_than_listing_them():
    """A one-second cadence over three hours is ten thousand ticks."""
    document = build(sample_run(), label_set(122))
    observations = document["results"][0]["observations"]

    assert observations["count"] > 0
    assert observations["threshold_crossings"] >= observations["candidates"]
    assert observations["max_total_score"] is not None
    assert isinstance(observations, dict)


def test_a_report_carries_the_metrics_and_the_matches():
    document = build(sample_run(), label_set(122))
    result = document["results"][0]

    assert result["metrics"]["ground_truth_moments"] == 1
    assert result["metrics"]["has_ground_truth"] is True
    assert result["candidate_timestamps"]
    assert result["matches"]
    assert "delta_seconds" in result["matches"][0]


def test_an_unmatched_label_and_candidate_are_both_reported():
    document = build(sample_run(), label_set(9000))
    result = document["results"][0]

    assert result["unmatched_label_timestamps"]
    assert result["unmatched_candidate_timestamps"]


def test_a_report_is_json_serializable():
    document = build(sample_run(), label_set(122))

    assert json.loads(json.dumps(document)) == document


def test_a_report_with_no_results_is_still_well_formed():
    document = report.build_report(
        session_id=42,
        labels=label_set(),
        results=[],
        cadence_seconds=1.0,
        generated_at=timezone.now(),
    )

    assert document["results"] == []
    assert document["session"]["message_count"] == 0


# -- privacy -------------------------------------------------------------------


def test_no_chat_text_or_chatter_identity_reaches_a_report():
    """Walked in full, so a value nested anywhere is still caught."""
    document = build(sample_run(), label_set(122))
    serialized = json.dumps(document)

    for forbidden in FORBIDDEN_VALUES:
        assert forbidden not in serialized


def test_no_report_key_names_an_identity_or_a_secret():
    document = build(sample_run(), label_set(122))

    keys = {key for key, _ in walk(document) if key is not None}
    for forbidden in FORBIDDEN_KEYS:
        assert forbidden not in keys


def test_the_only_strings_in_a_report_are_names_and_timestamps():
    """Nothing free-form survives, so no chat can hide in a string field."""
    document = build(sample_run(), label_set(122))

    strings = {value for _, value in walk(document) if isinstance(value, str)}
    for value in strings:
        assert value == "baseline" or _looks_like_a_timestamp(value)


def test_a_label_note_is_not_written_into_a_report():
    """Notes are the operator's own shorthand and may quote anything."""
    labels = LabelSet(
        session_id=42,
        default_tolerance_seconds=10.0,
        labels=(
            Label(
                timestamp=at(122),
                tolerance_seconds=10.0,
                note="someone said something very specific",
            ),
        ),
    )

    serialized = json.dumps(build(sample_run(), labels))

    assert "someone said something very specific" not in serialized


# -- writing -------------------------------------------------------------------


def test_writing_a_report_creates_its_directory(tmp_path):
    destination = tmp_path / "nested" / "report.json"

    written = report.write_report(build(sample_run(), label_set(122)), destination)

    assert written.exists()
    assert json.loads(written.read_text(encoding="utf-8"))["schema_version"] == 1


def _looks_like_a_timestamp(value: str) -> bool:
    from django.utils.dateparse import parse_datetime

    try:
        return parse_datetime(value) is not None
    except ValueError:
        return False
