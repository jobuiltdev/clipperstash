"""Named and file-supplied detector configurations for evaluation.

The detector already takes its calibration as an argument everywhere, so
nothing here changes how scoring works. This module only gives an operator two
extra ways to *name* a calibration: the built-in baseline, and a small JSON file
describing an experiment.

Two rules run through it:

* **The baseline is the production default, not a copy of it.** `named_config`
  returns `DEFAULT_CONFIG` itself, so the baseline cannot drift away from what
  the pipeline actually runs.
* **Nothing is guessed.** An unknown field, a malformed number or weights that
  do not sum to one are refused. Silently normalizing them would produce scores
  that look ordinary and mean something different.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from apps.moments.detector.config import DEFAULT_CONFIG, DetectorConfig, DetectorConfigError
from apps.moments.evaluation.errors import ConfigFileError

BASELINE_NAME = "baseline"

# Every scalar an experiment may set, and the type it must be. Anything else in
# the file is an error rather than something to ignore — a misspelled key that
# was quietly dropped would produce a run labelled as an experiment that was
# really just the baseline again.
_SCALAR_FIELDS: dict[str, type] = {
    "candidate_threshold": float,
    "cooldown_seconds": int,
    "baseline_window_seconds": int,
    "current_window_seconds": int,
    "minimum_current_messages": int,
    "minimum_current_chatters": int,
}

# The file's names for the tunables, mapped onto the dataclass's own names. The
# file uses the same vocabulary the dashboard and docs use.
_FIELD_TO_ATTRIBUTE: dict[str, str] = {
    "candidate_threshold": "candidate_threshold",
    "cooldown_seconds": "moment_cooldown_seconds",
    "baseline_window_seconds": "baseline_window_seconds",
    "current_window_seconds": "current_window_seconds",
    "minimum_current_messages": "min_current_messages",
    "minimum_current_chatters": "min_current_unique_chatters",
}

_WEIGHT_TO_ATTRIBUTE: dict[str, str] = {
    "velocity": "velocity_weight",
    "reaction": "reaction_weight",
    "diversity": "diversity_weight",
    "emote": "emote_weight",
    "absolute_activity": "absolute_activity_weight",
}

_TOP_LEVEL_FIELDS = frozenset({"name", "weights", *_SCALAR_FIELDS})


@dataclass(frozen=True)
class NamedConfig:
    """A detector calibration with a name to report it under."""

    name: str
    config: DetectorConfig

    def as_dict(self) -> dict[str, Any]:
        """The calibration as it appears in reports. Values, not object ids."""
        return {
            "name": self.name,
            "candidate_threshold": self.config.candidate_threshold,
            "cooldown_seconds": self.config.moment_cooldown_seconds,
            "current_window_seconds": self.config.current_window_seconds,
            "baseline_window_seconds": self.config.baseline_window_seconds,
            "minimum_current_messages": self.config.min_current_messages,
            "minimum_current_chatters": self.config.min_current_unique_chatters,
            "weights": self.config.weights(),
        }


def baseline() -> NamedConfig:
    """The calibration the pipeline runs on today.

    Returns `DEFAULT_CONFIG` itself rather than a reconstruction, so a change to
    the production default is a change to the baseline by definition and cannot
    be missed.
    """
    return NamedConfig(name=BASELINE_NAME, config=DEFAULT_CONFIG)


def named_config(name: str) -> NamedConfig:
    """Look up a built-in configuration by name.

    Only the baseline is built in. Experiments live in files, deliberately: a
    calibration that mattered enough to name in code would be a production
    change, and those need evidence and review rather than a constant.
    """
    if name == BASELINE_NAME:
        return baseline()
    raise ConfigFileError(
        f"Unknown configuration {name!r}. The only built-in name is {BASELINE_NAME!r}; "
        "supply anything else as a configuration file."
    )


def load_config_file(path: str | Path) -> NamedConfig:
    """Read one experimental calibration from JSON.

    Only the fields present are changed; everything else — saturation points,
    the reaction lexicon — stays as the baseline, so an experiment varies what
    it says it varies and nothing more.
    """
    location = Path(path)
    try:
        raw = location.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigFileError(f"Could not read the configuration file at {location}.") from exc

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigFileError(
            f"The configuration file at {location} is not valid JSON: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})."
        ) from exc

    return config_from_document(document, source=str(location))


def config_from_document(document: Any, *, source: str = "<config>") -> NamedConfig:
    """Validate one already-parsed configuration document."""
    if not isinstance(document, dict):
        raise ConfigFileError(f"{source}: a configuration must be a JSON object.")

    unknown = sorted(set(document) - _TOP_LEVEL_FIELDS)
    if unknown:
        raise ConfigFileError(
            f"{source}: unknown configuration field(s) {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(_TOP_LEVEL_FIELDS))}."
        )

    name = document.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigFileError(f"{source}: 'name' is required and must be a non-empty string.")
    name = name.strip()
    if name == BASELINE_NAME:
        raise ConfigFileError(
            f"{source}: {BASELINE_NAME!r} is reserved for the production default, so an "
            "experiment cannot claim it. Every comparison would otherwise show two "
            "different calibrations under one name."
        )

    overrides: dict[str, Any] = {}

    for field, expected in _SCALAR_FIELDS.items():
        if field not in document:
            continue
        overrides[_FIELD_TO_ATTRIBUTE[field]] = _read_number(
            document[field], field=field, expected=expected, source=source
        )

    if "weights" in document:
        overrides.update(_read_weights(document["weights"], source=source))

    try:
        config = replace(DEFAULT_CONFIG, **overrides)
    except DetectorConfigError as exc:
        raise ConfigFileError(f"{source}: {exc}") from exc

    return NamedConfig(name=name, config=config)


def _read_number(value: Any, *, field: str, expected: type, source: str) -> float | int:
    # `bool` is a subclass of `int`, and `True` as a threshold is a mistake
    # rather than the number one.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigFileError(f"{source}: {field!r} must be a number, got {value!r}.")
    if expected is int:
        if isinstance(value, float) and not value.is_integer():
            raise ConfigFileError(f"{source}: {field!r} must be a whole number, got {value!r}.")
        return int(value)
    return float(value)


def _read_weights(value: Any, *, source: str) -> dict[str, float]:
    """Weights are all-or-nothing.

    A partial override would leave the rest at their baseline values and quietly
    break the sum-to-one invariant, so every weight must be given together.
    """
    if not isinstance(value, dict):
        raise ConfigFileError(f"{source}: 'weights' must be a JSON object.")

    unknown = sorted(set(value) - set(_WEIGHT_TO_ATTRIBUTE))
    if unknown:
        raise ConfigFileError(
            f"{source}: unknown weight(s) {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(_WEIGHT_TO_ATTRIBUTE))}."
        )

    missing = sorted(set(_WEIGHT_TO_ATTRIBUTE) - set(value))
    if missing:
        raise ConfigFileError(
            f"{source}: 'weights' must set every weight together; missing {', '.join(missing)}. "
            "A partial override would leave the rest at their baseline values and no "
            "longer sum to 1.0."
        )

    return {
        _WEIGHT_TO_ATTRIBUTE[name]: _read_number(
            value[name], field=f"weights.{name}", expected=float, source=source
        )
        for name in _WEIGHT_TO_ATTRIBUTE
    }
