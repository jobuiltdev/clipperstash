"""Evaluate one stream session's chat for a clip-worthy moment.

A single explicit evaluation, run by a person. Nothing schedules this, chat
ingestion does not call it, and it creates no clips. It exists so the detector
can be exercised and, with `--at`, replayed deterministically over chat that was
collected earlier.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.moments import services
from apps.moments.detector import DEFAULT_CONFIG
from apps.monitoring.models import StreamSession


class Command(BaseCommand):
    help = (
        "Score a stream session's recent chat and record a moment candidate if it "
        "qualifies. Evaluates once and exits; no clip is created."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("session_id", type=int, help="Stream session id to evaluate.")
        parser.add_argument(
            "--at",
            dest="at",
            default=None,
            help=(
                "Evaluate as at this ISO 8601 instant instead of now, for replaying "
                "collected chat. Must include a timezone, e.g. 2026-09-04T18:32:10Z."
            ),
        )
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Score and report without recording a candidate.",
        )

    def handle(self, *args, **options) -> None:
        session = (
            StreamSession.objects.filter(pk=options["session_id"])
            .select_related("streamer")
            .first()
        )
        if session is None:
            raise CommandError(f"No stream session with id {options['session_id']}.")

        evaluation_time = self._evaluation_time(options.get("at"))
        config = DEFAULT_CONFIG

        result = services.evaluate_session(
            session,
            at=evaluation_time,
            persist=not options["no_persist"],
            config=config,
        )
        score = result.score

        self.stdout.write(
            f"Session {session.pk} ({session.streamer.username}) at {evaluation_time.isoformat()}"
        )
        self.stdout.write(
            f"  current window  {score.bounds.current_start.isoformat()} .. "
            f"{score.bounds.current_end.isoformat()}"
        )
        self.stdout.write(
            f"  baseline window {score.bounds.baseline_start.isoformat()} .. "
            f"{score.bounds.baseline_end.isoformat()}"
        )

        self.stdout.write("")
        self.stdout.write("Signals                     current   baseline")
        self._metric("messages", score.current.message_count, score.baseline.message_count)
        self._metric(
            "unique chatters", score.current.unique_chatters, score.baseline.unique_chatters
        )
        self._metric("emotes", score.current.emote_count, score.baseline.emote_count)
        self._metric(
            "reaction messages",
            score.current.reaction_message_count,
            score.baseline.reaction_message_count,
        )
        self.stdout.write(f"  velocity ratio            {score.velocity_ratio:>7.3f}")

        self.stdout.write("")
        self.stdout.write("Component scores (0-1)")
        for name, value in score.components.as_dict().items():
            weight = config.weights()[name]
            self.stdout.write(f"  {name:<24}{value:>7.3f}   (weight {weight:.2f})")

        self.stdout.write("")
        self.stdout.write(f"Total score               {score.total:>7.2f} / 100")
        self.stdout.write(
            f"  activity gate           {'passed' if score.activity_gate_passed else 'not met'}"
            f"  (needs >= {config.min_current_messages} messages from "
            f">= {config.min_current_unique_chatters} chatters)"
        )
        self.stdout.write(f"  candidate threshold     {config.candidate_threshold:.0f}")

        self._report_outcome(result, options["no_persist"], config)

    # -- helpers --------------------------------------------------------------

    def _evaluation_time(self, raw: str | None):
        if not raw:
            return timezone.now()

        try:
            parsed = parse_datetime(raw)
        except ValueError:
            parsed = None
        if parsed is None:
            raise CommandError(f"Could not read --at value {raw!r} as an ISO 8601 timestamp.")
        if parsed.tzinfo is None:
            raise CommandError("--at must include a timezone, e.g. 2026-09-04T18:32:10Z.")
        return parsed

    def _metric(self, label: str, current: int, baseline: int) -> None:
        self.stdout.write(f"  {label:<24}{current:>7}   {baseline:>8}")

    def _report_outcome(self, result, no_persist: bool, config) -> None:
        self.stdout.write("")
        if not result.score.qualifies:
            self.stdout.write("Did not qualify; no candidate recorded.")
            return

        if result.blocked_by_cooldown:
            self.stdout.write(
                self.style.WARNING(
                    "Qualified, but a candidate was already recorded within the last "
                    f"{config.moment_cooldown_seconds}s; none recorded."
                )
            )
            return

        if no_persist:
            self.stdout.write(
                self.style.WARNING("Qualified, but --no-persist was given; none recorded.")
            )
            return

        self.stdout.write(
            self.style.SUCCESS(f"Qualified. Recorded moment candidate {result.candidate.pk}.")
        )
