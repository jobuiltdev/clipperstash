"""Turning raw signals into component scores and one total.

Every component is scaled into 0..1 against a saturation point from the config,
so the weighted total is also 0..1 and is finally published on a 0-100 scale.
The arithmetic is deliberately simple and inspectable: each component is
persisted alongside the total, so a surprising score can always be explained by
looking at which part produced it.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.moments.detector.config import DEFAULT_CONFIG, DetectorConfig
from apps.moments.detector.signals import WindowSignals

PUBLIC_SCALE = 100.0

# Persisted precision. Enough to distinguish scores, not so much that stored
# diagnostics pretend to an accuracy the signals do not have.
COMPONENT_PRECISION = 4
TOTAL_PRECISION = 2
RATIO_PRECISION = 4


@dataclass(frozen=True)
class ComponentScores:
    """The five parts of a total, each already scaled into 0..1."""

    velocity: float
    reaction: float
    diversity: float
    emote: float
    absolute_activity: float

    def as_dict(self) -> dict[str, float]:
        return {
            "velocity": self.velocity,
            "reaction": self.reaction,
            "diversity": self.diversity,
            "emote": self.emote,
            "absolute_activity": self.absolute_activity,
        }


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def saturating(value: float, saturation: float) -> float:
    """Scale `value` into 0..1, reaching 1 at `saturation`."""
    if saturation <= 0:
        return 0.0
    return clamp(value / saturation)


def velocity_ratio(
    current: WindowSignals,
    baseline: WindowSignals,
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> float:
    """How much busier the current window is than the baseline.

    The baseline rate is floored, so a silent baseline yields a large but finite
    ratio instead of a division by zero or an automatic maximum.
    """
    baseline_rate = max(baseline.messages_per_second, config.min_baseline_rate_per_second)
    return current.messages_per_second / baseline_rate


def velocity_score(
    current: WindowSignals,
    baseline: WindowSignals,
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> float:
    """Relative speed-up, tempered by how much traffic backs it up.

    Two terms, multiplied. The first is how far above baseline the current rate
    is. The second is confidence: a ratio computed from one message says almost
    nothing, so it is scaled down until there is enough volume to mean
    something. This is what stops "0 messages, then 1" from reading as viral
    while "0 messages, then 30" still does.
    """
    ratio = velocity_ratio(current, baseline, config=config)

    span = config.velocity_saturation_ratio - 1.0
    relative = 0.0 if span <= 0 else clamp((ratio - 1.0) / span)

    confidence = saturating(current.message_count, config.velocity_confidence_messages)
    return clamp(relative * confidence)


def diversity_score(
    current: WindowSignals,
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> float:
    """How broadly the room is talking, rather than how loudly one person is.

    Half participation — distinct chatters per message, so one person sending
    everything scores near zero — and half absolute breadth, so a genuinely
    large crowd is recognized.
    """
    participation = clamp(current.chatter_participation)
    breadth = saturating(current.unique_chatters, config.diversity_saturation_chatters)
    return clamp((participation + breadth) / 2.0)


def emote_score(
    current: WindowSignals,
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> float:
    """Emote intensity, from Twitch's own fragment counts.

    Half density (emotes per message) and half coverage (share of messages
    carrying any), so a few enormous emote walls and a broad light sprinkle both
    register without either dominating. No emote name is ever inspected.
    """
    density = saturating(current.emotes_per_message, config.emote_saturation_per_message)
    coverage = clamp(current.emote_coverage)
    return clamp((density + coverage) / 2.0)


def reaction_score(
    current: WindowSignals,
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> float:
    """How much of the room is reacting, constrained by how many people are.

    The ratio of reacting messages is multiplied by reaction breadth, so one
    person typing "LMAO" twenty times cannot look like twenty people reacting
    once each.
    """
    ratio = clamp(current.reaction_ratio)
    breadth = saturating(current.unique_reacting_chatters, config.reaction_saturation_chatters)
    return clamp(ratio * breadth)


def absolute_activity_score(
    current: WindowSignals,
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> float:
    """Raw volume, independent of any comparison."""
    return saturating(current.message_count, config.absolute_activity_saturation_messages)


def component_scores(
    current: WindowSignals,
    baseline: WindowSignals,
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> ComponentScores:
    return ComponentScores(
        velocity=round(velocity_score(current, baseline, config=config), COMPONENT_PRECISION),
        reaction=round(reaction_score(current, config=config), COMPONENT_PRECISION),
        diversity=round(diversity_score(current, config=config), COMPONENT_PRECISION),
        emote=round(emote_score(current, config=config), COMPONENT_PRECISION),
        absolute_activity=round(
            absolute_activity_score(current, config=config), COMPONENT_PRECISION
        ),
    )


def total_score(
    components: ComponentScores,
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> float:
    """Weighted sum, published on a 0-100 scale."""
    weights = config.weights()
    weighted = sum(value * weights[name] for name, value in components.as_dict().items())
    return round(clamp(weighted) * PUBLIC_SCALE, TOTAL_PRECISION)
