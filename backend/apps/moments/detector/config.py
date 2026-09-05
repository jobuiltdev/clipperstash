"""Every tunable the detector has, in one place.

These are initial calibration values, not product truth. They were chosen to be
explainable rather than optimal, and are expected to move once Milestone 8
replays real collected streams. Nothing outside this module should hardcode a
window, weight, threshold or saturation point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# The weights are floats, so their sum is 0.4 + 0.2 + 0.15 + 0.15 + 0.1 ==
# 1.0000000000000002 rather than exactly 1.0. The invariant is "these weights
# describe a whole", not "these bits are identical", so it is checked with a
# tolerance rather than with `==`.
WEIGHT_SUM_TOLERANCE = 1e-9


class DetectorConfigError(ValueError):
    """A detector calibration that would produce meaningless scores."""


@dataclass(frozen=True)
class DetectorConfig:
    """A complete detector calibration.

    Passed explicitly rather than read from globals, so a test — or a future
    per-streamer calibration — can vary one number without touching the code
    that uses it.
    """

    # -- windows -------------------------------------------------------------
    # The most recent `current_window_seconds` are compared against the
    # `baseline_window_seconds` immediately before them. The two never overlap.
    current_window_seconds: int = 10
    baseline_window_seconds: int = 60

    # -- activity gate -------------------------------------------------------
    # Below these, the window is too quiet to say anything about, so no
    # candidate is emitted however the arithmetic comes out.
    min_current_messages: int = 5
    min_current_unique_chatters: int = 3

    # -- decision ------------------------------------------------------------
    # On the published 0-100 scale.
    candidate_threshold: float = 70.0
    # Future-facing only. Milestone 5 never acts on it; clip creation does not
    # exist yet, and this constant creates none.
    auto_clip_threshold: float = 80.0

    # How long after a candidate the same session stays quiet. Prevents one
    # burst from producing a row per evaluation.
    moment_cooldown_seconds: int = 45

    # -- weights -------------------------------------------------------------
    # Must sum to 1.0; a test asserts it.
    velocity_weight: float = 0.40
    reaction_weight: float = 0.20
    diversity_weight: float = 0.15
    emote_weight: float = 0.15
    absolute_activity_weight: float = 0.10

    # -- saturation points ---------------------------------------------------
    # Each signal is scaled into 0..1 by the point at which "more" stops
    # meaning anything useful.

    # A floor on the baseline rate, in messages per second. Without it a silent
    # baseline would make every ratio infinite; with it, a quiet channel still
    # has to produce real volume to score.
    min_baseline_rate_per_second: float = 0.05
    # The velocity ratio at which the relative term is fully saturated.
    velocity_saturation_ratio: float = 4.0
    # Messages in the current window needed to fully trust a ratio. Below this,
    # velocity is scaled down: one message against silence is not a spike.
    velocity_confidence_messages: int = 10

    # Distinct chatters at which breadth is fully saturated.
    diversity_saturation_chatters: int = 15
    # Emotes per message at which emote intensity is fully saturated.
    emote_saturation_per_message: float = 2.0
    # Distinct reacting chatters at which reaction breadth is fully saturated.
    reaction_saturation_chatters: int = 5
    # Messages in the current window at which raw activity is fully saturated.
    absolute_activity_saturation_messages: int = 30

    # -- reaction lexicon ----------------------------------------------------
    # Single tokens. Matched against normalized whole tokens, never as
    # substrings, so "what" does not fire inside "whatever".
    reaction_tokens: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "lol",
                "lmao",
                "lmfao",
                "rofl",
                "omg",
                "wtf",
                "what",
                "insane",
                "crazy",
                "clip",
                "clipped",
                "w",
                "huge",
                "yo",
                "bruh",
                "damn",
                "wow",
                "sheesh",
                "poggers",
                "pog",
            }
        )
    )

    # Multi-token phrases, matched against consecutive normalized tokens.
    reaction_phrases: tuple[tuple[str, ...], ...] = (
        ("no", "way"),
        ("clip", "it"),
        ("clip", "that"),
        ("what", "the"),
        ("oh", "my", "god"),
        ("holy", "shit"),
    )

    # Tokens whose final character may be repeated for emphasis, so "lollll"
    # and "yooooo" collapse to "lol" and "yo". Deliberately a short list: this
    # is a normalization rule, not a spelling corrector.
    elongatable_tokens: frozenset[str] = field(
        default_factory=lambda: frozenset({"lol", "lmao", "yo", "wow", "omg", "no", "w"})
    )

    def __post_init__(self) -> None:
        """Refuse a calibration that cannot produce a meaningful score.

        Checked on construction, so an experimental configuration is rejected
        where it is written rather than silently producing scores nobody can
        interpret. `dataclasses.replace` re-runs this, so a config derived from
        the default is validated too.
        """
        self.validate()

    def validate(self) -> None:
        for name, value in (
            ("current_window_seconds", self.current_window_seconds),
            ("baseline_window_seconds", self.baseline_window_seconds),
        ):
            if value <= 0:
                raise DetectorConfigError(f"{name} must be greater than zero, got {value!r}.")

        for name, value in (
            ("min_current_messages", self.min_current_messages),
            ("min_current_unique_chatters", self.min_current_unique_chatters),
            ("moment_cooldown_seconds", self.moment_cooldown_seconds),
        ):
            if value < 0:
                raise DetectorConfigError(f"{name} must not be negative, got {value!r}.")

        for name, value in (
            ("candidate_threshold", self.candidate_threshold),
            ("auto_clip_threshold", self.auto_clip_threshold),
        ):
            # The total is published on a 0-100 scale, so a threshold outside it
            # is either unreachable or always met.
            if not 0.0 <= value <= 100.0:
                raise DetectorConfigError(f"{name} must be between 0 and 100, got {value!r}.")

        weights = self.weights()
        for name, weight in weights.items():
            if weight < 0:
                raise DetectorConfigError(
                    f"The {name} weight must not be negative, got {weight!r}."
                )

        total = sum(weights.values())
        if not math.isclose(total, 1.0, abs_tol=WEIGHT_SUM_TOLERANCE):
            raise DetectorConfigError(
                f"The weights must sum to 1.0, got {total!r}. "
                "They are not normalized automatically: a silent rescale would change "
                "every score without saying so."
            )

        for name, value in (
            ("min_baseline_rate_per_second", self.min_baseline_rate_per_second),
            ("velocity_saturation_ratio", self.velocity_saturation_ratio),
            ("velocity_confidence_messages", self.velocity_confidence_messages),
            ("diversity_saturation_chatters", self.diversity_saturation_chatters),
            ("emote_saturation_per_message", self.emote_saturation_per_message),
            ("reaction_saturation_chatters", self.reaction_saturation_chatters),
            ("absolute_activity_saturation_messages", self.absolute_activity_saturation_messages),
        ):
            if value <= 0:
                raise DetectorConfigError(f"{name} must be greater than zero, got {value!r}.")

        if self.velocity_saturation_ratio <= 1.0:
            raise DetectorConfigError(
                "velocity_saturation_ratio must exceed 1.0; at or below it the relative "
                "term is always zero and velocity stops meaning anything."
            )

    def weights(self) -> dict[str, float]:
        return {
            "velocity": self.velocity_weight,
            "reaction": self.reaction_weight,
            "diversity": self.diversity_weight,
            "emote": self.emote_weight,
            "absolute_activity": self.absolute_activity_weight,
        }


DEFAULT_CONFIG = DetectorConfig()
