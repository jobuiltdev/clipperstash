"""Tests for `GET /api/dashboard/detector-config/`.

The point of the endpoint is that the interface never restates a threshold of
its own, so these tests compare the response against the detector's own
configuration object rather than against literals.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.clips.config import DEFAULT_CONFIG as CLIP_CONFIG
from apps.moments.detector import DEFAULT_CONFIG as DETECTOR_CONFIG

URL = reverse("dashboard:detector-config")


def test_the_calibration_comes_from_the_detector_itself(client):
    payload = client.get(URL).json()["detector"]

    assert payload["candidate_threshold"] == DETECTOR_CONFIG.candidate_threshold
    assert payload["current_window_seconds"] == DETECTOR_CONFIG.current_window_seconds
    assert payload["baseline_window_seconds"] == DETECTOR_CONFIG.baseline_window_seconds
    assert payload["cooldown_seconds"] == DETECTOR_CONFIG.moment_cooldown_seconds
    assert payload["minimum_current_messages"] == DETECTOR_CONFIG.min_current_messages
    assert payload["minimum_current_chatters"] == DETECTOR_CONFIG.min_current_unique_chatters


def test_the_weights_are_the_detectors_weights(client):
    payload = client.get(URL).json()["detector"]

    assert payload["weights"] == DETECTOR_CONFIG.weights()
    assert sum(payload["weights"].values()) == pytest.approx(1.0)


def test_the_clip_timing_bounds_are_published_too(client):
    """An operator seeing a stale-moment refusal needs the budget it was judged against."""
    payload = client.get(URL).json()["detector"]

    assert payload["clip_freshness_seconds"] == CLIP_CONFIG.max_candidate_age_seconds
    assert payload["clip_verification_timeout_seconds"] == CLIP_CONFIG.verification_timeout_seconds


def test_the_endpoint_needs_no_database(client):
    """It reads constants, so it works before anything has been observed."""
    assert client.get(URL).status_code == 200


def test_no_lexicon_or_saturation_internals_leak_into_the_response(client):
    """The interface shows calibration, not the detector's whole implementation."""
    payload = client.get(URL).json()["detector"]

    assert "reaction_tokens" not in payload
    assert "reaction_phrases" not in payload
    assert "elongatable_tokens" not in payload
