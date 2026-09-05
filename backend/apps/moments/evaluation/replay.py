"""Deterministic offline replay of the detector over collected chat.

This is the heart of the evaluation layer, and it is defined by what it does
*not* do. It does not write a row, it does not touch Twitch, and it does not
contain any scoring logic of its own: every number it reports comes from
`score_samples`, the same function the production service calls, given the
original `ChatSample` rows exactly as they were collected.

**One scoring truth, taken literally.** Replay knows nothing about how the
detector reads a message. It does not know which words count as a reaction,
which signals inspect text, or how text could be rewritten while preserving a
score. It hands over the samples and records the answer. An earlier version
substituted a reaction-equivalent stand-in text to make the per-window work
cheaper; that was removed, because it made the evaluator hold an opinion about
detector-internal semantics, and a signal added later that read text differently
would have drifted silently away from production. Replay is slower for it and
correct by construction.

**Cadence.** The detector has no natural evaluation moment — production runs it
when a person asks. Replay therefore imposes a fixed grid: every
`cadence_seconds` from the first message to the last, aligned to whole seconds.
The cadence materially changes the results (a coarser grid can step over a short
spike; a finer one reports the same spike many times), so it is recorded in
every report and printed by the command.

**Cooldown.** Production suppresses a candidate when a `MomentCandidate` row
already exists within the cooldown. Replay writes no rows, so it simulates that
against the candidates it would itself have created, using the same boundary
rule as `services.is_in_cooldown`.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.moments.detector import ChatSample, DetectorConfig
from apps.moments.detector.config import DEFAULT_CONFIG
from apps.moments.detector.detector import score_samples
from apps.moments.evaluation.errors import ReplayError
from apps.monitoring.models import ChatMessage, StreamSession

DEFAULT_CADENCE_SECONDS = 1.0

# Cheap protection against an operator asking for a grid that would take hours
# and produce a report nobody can read. A day of chat at one-second cadence is
# 86,400 ticks; this allows an order of magnitude more.
MAX_TICKS = 1_000_000

# Reasons the activity gate refused a window. Reported so a quiet stretch can be
# told apart from a busy one that simply scored low.
GATE_TOO_FEW_MESSAGES = "too_few_messages"
GATE_TOO_FEW_CHATTERS = "too_few_chatters"
GATE_TOO_FEW_MESSAGES_AND_CHATTERS = "too_few_messages_and_chatters"


@dataclass(frozen=True)
class Observation:
    """One evaluation tick, as aggregates only.

    Carries no message text and no chatter identity — only counts of them. This
    is the unit a report is built from, so anything present here can end up in a
    file on an operator's disk.
    """

    evaluated_at: datetime

    gate_passed: bool
    gate_reason: str | None

    total_score: float
    threshold: float
    threshold_crossed: bool

    velocity_score: float
    reaction_score: float
    diversity_score: float
    emote_score: float
    absolute_activity_score: float
    velocity_ratio: float

    current_message_count: int
    baseline_message_count: int
    current_unique_chatter_count: int
    baseline_unique_chatter_count: int
    current_emote_count: int
    baseline_emote_count: int
    current_reaction_count: int
    baseline_reaction_count: int

    would_create_candidate: bool
    cooldown_suppressed: bool

    # False while the tick is close enough to the start of collection that the
    # baseline window extends past the first message collected. Velocity is
    # inflated there — a nearly empty baseline is floored rather than zero, so
    # any activity looks like a surge — and it is an artefact of when monitoring
    # was started, not of the broadcast. Production has the same blind spot when
    # a session opens; calibration should usually ignore these ticks, which
    # `require_complete_baseline` does.
    baseline_complete: bool

    @property
    def qualifies(self) -> bool:
        """What production calls `qualifies`: past the gate and past the threshold.

        Distinct from `would_create_candidate`, which additionally accounts for
        cooldown. Keeping them apart is the point of the whole exercise: a
        suppressed crossing still tells you the detector saw the moment.
        """
        return self.gate_passed and self.threshold_crossed


@dataclass(frozen=True)
class ReplayResult:
    """Every tick of one replay, plus the grid it was produced on."""

    observations: tuple[Observation, ...]
    cadence_seconds: float
    first_tick: datetime | None
    last_tick: datetime | None
    sample_count: int

    @property
    def candidates(self) -> tuple[Observation, ...]:
        """Ticks that would have produced a `MomentCandidate` in production."""
        return tuple(obs for obs in self.observations if obs.would_create_candidate)

    @property
    def candidate_timestamps(self) -> tuple[datetime, ...]:
        return tuple(obs.evaluated_at for obs in self.candidates)

    @property
    def threshold_crossings(self) -> tuple[Observation, ...]:
        """Ticks the detector judged clip-worthy, cooldown notwithstanding."""
        return tuple(obs for obs in self.observations if obs.qualifies)

    @property
    def suppressed(self) -> tuple[Observation, ...]:
        return tuple(obs for obs in self.observations if obs.cooldown_suppressed)

    @property
    def warmup_observations(self) -> tuple[Observation, ...]:
        """Ticks whose baseline window reaches back past the collected chat."""
        return tuple(obs for obs in self.observations if not obs.baseline_complete)

    @property
    def duration_seconds(self) -> float:
        if self.first_tick is None or self.last_tick is None:
            return 0.0
        return (self.last_tick - self.first_tick).total_seconds()


def load_session_samples(session: StreamSession) -> list[ChatSample]:
    """Read one session's chat in deterministic chronological order.

    Reads only the four columns scoring uses, in one query, ordered by
    `(timestamp, id)` so two messages sharing an instant always replay in the
    same order. This is a read: nothing is written, and the session is not
    touched.
    """
    rows = (
        ChatMessage.objects.filter(session=session)
        .order_by("timestamp", "id")
        .values_list("timestamp", "chatter_user_id_hash", "text", "emote_count")
    )
    return [
        ChatSample(
            timestamp=timestamp,
            chatter_hash=chatter_hash,
            text=text,
            emote_count=emote_count,
        )
        for timestamp, chatter_hash, text, emote_count in rows
    ]


def build_ticks(
    samples: Sequence[ChatSample],
    *,
    cadence_seconds: float = DEFAULT_CADENCE_SECONDS,
    not_before: datetime | None = None,
) -> list[datetime]:
    """The evaluation grid for one session.

    Aligned to whole seconds from the first message, stepping by the cadence,
    through to the first grid point at or after the last message. Aligning
    rather than starting at the first message's exact microsecond keeps the grid
    reproducible: re-collecting the same stream at slightly different
    sub-second offsets produces the same ticks.

    A cadence coarser than the current window can step over a spike entirely.
    That is a real property of the grid, not a bug, and it is why the cadence is
    reported alongside every result.
    """
    if cadence_seconds <= 0:
        raise ReplayError(
            f"The cadence must be greater than zero seconds, got {cadence_seconds!r}."
        )
    if not samples:
        return []

    step = timedelta(seconds=cadence_seconds)
    start = samples[0].timestamp.replace(microsecond=0)
    end = samples[-1].timestamp

    estimated = int((end - start).total_seconds() / cadence_seconds) + 2
    if estimated > MAX_TICKS:
        raise ReplayError(
            f"A cadence of {cadence_seconds}s over this session would need about "
            f"{estimated} evaluations, beyond the {MAX_TICKS} limit. Use a coarser cadence."
        )

    ticks: list[datetime] = []
    tick = start
    while tick < end:
        ticks.append(tick)
        tick += step
    # One more, so the final messages are inside a current window rather than
    # sitting past the last tick unevaluated.
    ticks.append(tick)

    if not_before is not None:
        ticks = [tick for tick in ticks if tick >= not_before]
    return ticks


def replay_samples(
    samples: Iterable[ChatSample],
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
    cadence_seconds: float = DEFAULT_CADENCE_SECONDS,
    require_complete_baseline: bool = False,
) -> ReplayResult:
    """Run the detector across the grid and record every tick.

    Pure: takes samples, returns observations. No database, no clock, no
    network. The same samples, config and cadence always give the same result.

    `require_complete_baseline` drops the opening ticks whose baseline window
    reaches back before the first collected message. Off by default, because
    production has exactly that blind spot when a session opens and hiding it
    would flatter the numbers. Turn it on when calibrating: those ticks measure
    when monitoring was started, not what the broadcast did.
    """
    ordered = sorted(samples, key=lambda sample: sample.timestamp)
    timestamps = [sample.timestamp for sample in ordered]

    span = timedelta(seconds=config.current_window_seconds + config.baseline_window_seconds)
    baseline_ready_at = ordered[0].timestamp + span if ordered else None

    ticks = build_ticks(
        ordered,
        cadence_seconds=cadence_seconds,
        not_before=baseline_ready_at if require_complete_baseline else None,
    )

    cooldown = timedelta(seconds=config.moment_cooldown_seconds)

    observations: list[Observation] = []
    last_candidate_at: datetime | None = None

    for tick in ticks:
        # Only the messages the two windows can possibly contain. Slicing here
        # rather than handing the whole session to the detector every tick is
        # what keeps replay linear in the number of messages. The samples
        # themselves are passed through untouched.
        low = bisect_right(timestamps, tick - span)
        high = bisect_right(timestamps, tick)
        score = score_samples(ordered[low:high], tick, config=config)

        # Mirrors `services.is_in_cooldown`: a candidate exactly the cooldown
        # length ago does not block; one a moment later does.
        suppressed = (
            score.qualifies
            and last_candidate_at is not None
            and last_candidate_at > tick - cooldown
        )
        creates = score.qualifies and not suppressed
        if creates:
            last_candidate_at = tick

        observations.append(
            Observation(
                evaluated_at=tick,
                gate_passed=score.activity_gate_passed,
                gate_reason=_gate_reason(score, config=config),
                total_score=score.total,
                threshold=score.threshold,
                threshold_crossed=score.total >= score.threshold,
                velocity_score=score.components.velocity,
                reaction_score=score.components.reaction,
                diversity_score=score.components.diversity,
                emote_score=score.components.emote,
                absolute_activity_score=score.components.absolute_activity,
                velocity_ratio=score.velocity_ratio,
                current_message_count=score.current.message_count,
                baseline_message_count=score.baseline.message_count,
                current_unique_chatter_count=score.current.unique_chatters,
                baseline_unique_chatter_count=score.baseline.unique_chatters,
                current_emote_count=score.current.emote_count,
                baseline_emote_count=score.baseline.emote_count,
                current_reaction_count=score.current.reaction_message_count,
                baseline_reaction_count=score.baseline.reaction_message_count,
                would_create_candidate=creates,
                cooldown_suppressed=suppressed,
                baseline_complete=baseline_ready_at is not None and tick >= baseline_ready_at,
            )
        )

    return ReplayResult(
        observations=tuple(observations),
        cadence_seconds=cadence_seconds,
        first_tick=ticks[0] if ticks else None,
        last_tick=ticks[-1] if ticks else None,
        sample_count=len(ordered),
    )


def replay_session(
    session: StreamSession,
    *,
    config: DetectorConfig = DEFAULT_CONFIG,
    cadence_seconds: float = DEFAULT_CADENCE_SECONDS,
    require_complete_baseline: bool = False,
) -> ReplayResult:
    """Replay one persisted session. Reads chat; writes nothing."""
    return replay_samples(
        load_session_samples(session),
        config=config,
        cadence_seconds=cadence_seconds,
        require_complete_baseline=require_complete_baseline,
    )


# -- internals ----------------------------------------------------------------


def _gate_reason(score, *, config: DetectorConfig) -> str | None:
    if score.activity_gate_passed:
        return None

    too_few_messages = score.current.message_count < config.min_current_messages
    too_few_chatters = score.current.unique_chatters < config.min_current_unique_chatters

    if too_few_messages and too_few_chatters:
        return GATE_TOO_FEW_MESSAGES_AND_CHATTERS
    if too_few_messages:
        return GATE_TOO_FEW_MESSAGES
    return GATE_TOO_FEW_CHATTERS
