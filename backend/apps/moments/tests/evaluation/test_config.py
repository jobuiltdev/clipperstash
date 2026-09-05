"""The detector calibration an evaluation runs on.

Two things are being protected here. The baseline must remain the production
default rather than a copy of it, and an invalid experiment must be refused
outright — a calibration that silently normalizes itself produces scores that
look ordinary and mean something else.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from apps.moments.detector import DEFAULT_CONFIG
from apps.moments.detector.config import DetectorConfigError
from apps.moments.evaluation import config as evaluation_config
from apps.moments.evaluation.errors import ConfigFileError


def write(tmp_path, document, name: str = "config.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


VALID = {
    "name": "example",
    "candidate_threshold": 72,
    "weights": {
        "velocity": 0.35,
        "reaction": 0.20,
        "diversity": 0.20,
        "emote": 0.15,
        "absolute_activity": 0.10,
    },
}


# -- the baseline -------------------------------------------------------------


def test_the_baseline_is_the_production_default_itself():
    """Not a reconstruction of it.

    A copy could drift: someone changes a production weight, the baseline keeps
    the old one, and every comparison silently measures against something the
    pipeline stopped running months ago.
    """
    assert evaluation_config.baseline().config is DEFAULT_CONFIG


def test_the_baseline_is_reachable_by_name():
    named = evaluation_config.named_config(evaluation_config.BASELINE_NAME)

    assert named.name == "baseline"
    assert named.config is DEFAULT_CONFIG


def test_an_unknown_built_in_name_is_refused():
    with pytest.raises(ConfigFileError, match="Unknown configuration"):
        evaluation_config.named_config("aggressive")


def test_the_reported_values_are_the_configs_own(tmp_path):
    reported = evaluation_config.baseline().as_dict()

    assert reported["candidate_threshold"] == DEFAULT_CONFIG.candidate_threshold
    assert reported["cooldown_seconds"] == DEFAULT_CONFIG.moment_cooldown_seconds
    assert reported["weights"] == DEFAULT_CONFIG.weights()


# -- loading an experiment ----------------------------------------------------


def test_a_valid_experiment_loads(tmp_path):
    named = evaluation_config.load_config_file(write(tmp_path, VALID))

    assert named.name == "example"
    assert named.config.candidate_threshold == 72.0
    assert named.config.diversity_weight == 0.20


def test_unspecified_tunables_stay_at_the_baseline(tmp_path):
    """An experiment varies what it names and nothing else."""
    named = evaluation_config.load_config_file(write(tmp_path, VALID))

    assert named.config.moment_cooldown_seconds == DEFAULT_CONFIG.moment_cooldown_seconds
    assert named.config.current_window_seconds == DEFAULT_CONFIG.current_window_seconds
    assert named.config.reaction_tokens == DEFAULT_CONFIG.reaction_tokens


def test_every_documented_field_can_be_set(tmp_path):
    document = {
        "name": "everything",
        "candidate_threshold": 65,
        "cooldown_seconds": 30,
        "baseline_window_seconds": 90,
        "current_window_seconds": 15,
        "minimum_current_messages": 8,
        "minimum_current_chatters": 4,
        "weights": VALID["weights"],
    }

    config = evaluation_config.load_config_file(write(tmp_path, document)).config

    assert config.candidate_threshold == 65.0
    assert config.moment_cooldown_seconds == 30
    assert config.baseline_window_seconds == 90
    assert config.current_window_seconds == 15
    assert config.min_current_messages == 8
    assert config.min_current_unique_chatters == 4


# -- refusing bad input -------------------------------------------------------


def test_an_unknown_field_is_refused_rather_than_ignored(tmp_path):
    """A misspelled key that was quietly dropped would label the baseline an experiment."""
    path = write(tmp_path, {**VALID, "candidate_threshhold": 90})

    with pytest.raises(ConfigFileError, match="unknown configuration field"):
        evaluation_config.load_config_file(path)


def test_an_unknown_weight_is_refused(tmp_path):
    path = write(tmp_path, {**VALID, "weights": {**VALID["weights"], "hype": 0.1}})

    with pytest.raises(ConfigFileError, match="unknown weight"):
        evaluation_config.load_config_file(path)


def test_a_partial_weight_override_is_refused(tmp_path):
    """Changing one weight alone would break the sum silently."""
    path = write(tmp_path, {"name": "partial", "weights": {"velocity": 0.5}})

    with pytest.raises(ConfigFileError, match="every weight together"):
        evaluation_config.load_config_file(path)


def test_weights_that_do_not_sum_to_one_are_refused(tmp_path):
    path = write(
        tmp_path,
        {
            "name": "unbalanced",
            "weights": {
                "velocity": 0.5,
                "reaction": 0.2,
                "diversity": 0.2,
                "emote": 0.15,
                "absolute_activity": 0.10,
            },
        },
    )

    with pytest.raises(ConfigFileError, match="must sum to 1.0"):
        evaluation_config.load_config_file(path)


def test_a_negative_weight_is_refused(tmp_path):
    path = write(
        tmp_path,
        {
            "name": "negative",
            "weights": {
                "velocity": -0.1,
                "reaction": 0.3,
                "diversity": 0.3,
                "emote": 0.3,
                "absolute_activity": 0.2,
            },
        },
    )

    with pytest.raises(ConfigFileError, match="must not be negative"):
        evaluation_config.load_config_file(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("candidate_threshold", 150, "between 0 and 100"),
        ("candidate_threshold", -1, "between 0 and 100"),
        ("cooldown_seconds", -5, "must not be negative"),
        ("current_window_seconds", 0, "greater than zero"),
        ("baseline_window_seconds", -30, "greater than zero"),
    ],
)
def test_impossible_values_are_refused(tmp_path, field, value, message):
    path = write(tmp_path, {"name": "broken", field: value})

    with pytest.raises(ConfigFileError, match=message):
        evaluation_config.load_config_file(path)


def test_a_whole_number_field_refuses_a_fraction(tmp_path):
    path = write(tmp_path, {"name": "fractional", "cooldown_seconds": 45.5})

    with pytest.raises(ConfigFileError, match="whole number"):
        evaluation_config.load_config_file(path)


def test_a_boolean_is_not_a_number(tmp_path):
    path = write(tmp_path, {"name": "boolish", "candidate_threshold": True})

    with pytest.raises(ConfigFileError, match="must be a number"):
        evaluation_config.load_config_file(path)


def test_an_experiment_may_not_call_itself_the_baseline(tmp_path):
    path = write(tmp_path, {**VALID, "name": "baseline"})

    with pytest.raises(ConfigFileError, match="reserved"):
        evaluation_config.load_config_file(path)


def test_a_nameless_configuration_is_refused(tmp_path):
    path = write(tmp_path, {"candidate_threshold": 72})

    with pytest.raises(ConfigFileError, match="'name' is required"):
        evaluation_config.load_config_file(path)


def test_malformed_json_is_reported_with_its_position(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"name": "x",}', encoding="utf-8")

    with pytest.raises(ConfigFileError, match="not valid JSON"):
        evaluation_config.load_config_file(str(path))


def test_a_missing_file_is_reported_clearly(tmp_path):
    with pytest.raises(ConfigFileError, match="Could not read"):
        evaluation_config.load_config_file(str(tmp_path / "absent.json"))


def test_a_json_array_is_not_a_configuration(tmp_path):
    path = tmp_path / "array.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ConfigFileError, match="must be a JSON object"):
        evaluation_config.load_config_file(str(path))


# -- the dataclass's own guard ------------------------------------------------


def test_the_config_validates_itself_on_construction():
    """`dataclasses.replace` runs the same guard, so derived configs are checked too."""
    with pytest.raises(DetectorConfigError, match="must sum to 1.0"):
        replace(DEFAULT_CONFIG, velocity_weight=0.9)


def test_a_saturation_point_of_zero_is_refused():
    with pytest.raises(DetectorConfigError, match="greater than zero"):
        replace(DEFAULT_CONFIG, diversity_saturation_chatters=0)


def test_a_velocity_saturation_at_or_below_one_is_refused():
    """At a ratio of 1.0 the relative term is always zero and velocity says nothing."""
    with pytest.raises(DetectorConfigError, match="velocity_saturation_ratio"):
        replace(DEFAULT_CONFIG, velocity_saturation_ratio=1.0)


def test_the_shipped_default_passes_its_own_validation():
    DEFAULT_CONFIG.validate()


def test_the_tracked_example_config_is_valid():
    """The example an operator copies must actually load."""
    from pathlib import Path

    example = (
        Path(__file__).resolve().parents[4] / "evaluation_data" / "examples" / "config.example.json"
    )
    named = evaluation_config.load_config_file(example)

    assert named.name == "example-higher-diversity"
