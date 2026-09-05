"""Replay a collected session against human labels and report how well they agree.

Read-only by construction. It loads chat, runs the detector in memory, compares
the result with a local label file and prints aggregates. It creates no
`MomentCandidate`, no `Clip` and no session; it changes nothing that already
exists; and it never contacts Twitch, so it can be run against an ended
broadcast as many times as an operator likes.

    python manage.py evaluate_moments 12 --labels evaluation_data/session-12.json

Nothing schedules this. As with every other stage of the pipeline, a person runs
it deliberately.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.moments.evaluation import comparison, replay, report
from apps.moments.evaluation import config as config_module
from apps.moments.evaluation.errors import EvaluationError
from apps.moments.evaluation.labels import load_labels
from apps.monitoring.models import StreamSession


class Command(BaseCommand):
    help = (
        "Replay a stream session's collected chat through the detector and compare "
        "the result with local ground-truth labels. Reads only: no candidate, no "
        "clip, no Twitch call, no database change."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("session_id", type=int, help="Stream session id to evaluate.")
        parser.add_argument(
            "--labels",
            required=True,
            help="Path to the local ground-truth label file for this session.",
        )
        parser.add_argument(
            "--config",
            default=config_module.BASELINE_NAME,
            help=(
                "Built-in configuration to evaluate. Only "
                f"{config_module.BASELINE_NAME!r} is built in; use --config-file for "
                "an experiment."
            ),
        )
        parser.add_argument(
            "--config-file",
            default=None,
            help="Evaluate this JSON configuration instead of the built-in one.",
        )
        parser.add_argument(
            "--compare",
            action="append",
            default=[],
            metavar="PATH",
            help=(
                "Also evaluate this JSON configuration and show it alongside. "
                "May be given more than once."
            ),
        )
        parser.add_argument(
            "--cadence-seconds",
            type=float,
            default=replay.DEFAULT_CADENCE_SECONDS,
            help=(
                "Seconds between evaluations. Changes the results, so it is reported "
                f"with them. Default {replay.DEFAULT_CADENCE_SECONDS:g}."
            ),
        )
        parser.add_argument(
            "--output",
            default=None,
            help="Write a JSON report to this local path. Reports are never committed.",
        )

    def handle(self, *args, **options) -> None:
        session = StreamSession.objects.filter(pk=options["session_id"]).first()
        if session is None:
            raise CommandError(f"No stream session with id {options['session_id']}.")

        cadence = options["cadence_seconds"]
        if cadence <= 0:
            raise CommandError(f"--cadence-seconds must be greater than zero, got {cadence!r}.")

        try:
            labels = load_labels(options["labels"], session_id=session.pk)
            configurations = self._configurations(options)
        except EvaluationError as exc:
            raise CommandError(str(exc)) from exc

        samples = replay.load_session_samples(session)
        if not samples:
            raise CommandError(
                f"Session {session.pk} has no collected chat, so there is nothing to "
                "replay. Collect a window with `monitor_chat` first."
            )

        try:
            results = comparison.compare_configurations(
                samples, labels, configurations, cadence_seconds=cadence
            )
        except EvaluationError as exc:
            raise CommandError(str(exc)) from exc

        self._report_session(session, samples, labels, cadence)
        for result in results:
            self._report_configuration(result)
        if len(results) > 1:
            self._report_ranking(results)

        if options["output"]:
            document = report.build_report(
                session_id=session.pk,
                labels=labels,
                results=results,
                cadence_seconds=cadence,
                generated_at=timezone.now(),
            )
            written = report.write_report(document, options["output"])
            self.stdout.write("")
            self.stdout.write(f"Report written to {written}")

    # -- configuration ---------------------------------------------------------

    def _configurations(self, options) -> list[config_module.NamedConfig]:
        primary = (
            config_module.load_config_file(options["config_file"])
            if options["config_file"]
            else config_module.named_config(options["config"])
        )

        configurations = [primary]
        seen = {primary.name}
        for path in options["compare"]:
            candidate = config_module.load_config_file(path)
            if candidate.name in seen:
                raise CommandError(
                    f"Two configurations are both named {candidate.name!r}. "
                    "Give each experiment its own name so the results can be told apart."
                )
            seen.add(candidate.name)
            configurations.append(candidate)
        return configurations

    # -- output ----------------------------------------------------------------
    #
    # Everything printed below is an aggregate. No message text, no chatter
    # hash, no Twitch id and no token appears in any line.

    def _report_session(self, session, samples, labels, cadence: float) -> None:
        first = samples[0].timestamp
        last = samples[-1].timestamp
        duration = (last - first).total_seconds()

        self.stdout.write(f"Session {session.pk}")
        self.stdout.write(f"  chat span         {first.isoformat()} .. {last.isoformat()}")
        self.stdout.write(f"  duration          {duration:.0f}s ({duration / 3600:.2f}h)")
        self.stdout.write(f"  messages          {len(samples)}")
        self.stdout.write(f"  labels            {len(labels)}")
        self.stdout.write(f"  cadence           {cadence:g}s between evaluations")

        if not labels:
            self.stdout.write(
                self.style.WARNING(
                    "  No labels supplied. Recall and F1 are reported as 0 because there is "
                    "nothing to find, not because the detector missed anything."
                )
            )

        overlapping = labels.overlapping_pairs
        if overlapping:
            self.stdout.write(
                self.style.WARNING(
                    f"  {len(overlapping)} pair(s) of labels have overlapping tolerance "
                    "windows; matching in the overlap is deterministic but ambiguous."
                )
            )

    def _report_configuration(self, result: comparison.ConfigurationResult) -> None:
        metrics = result.metrics
        settings = result.configuration.as_dict()

        self.stdout.write("")
        self.stdout.write(f"Configuration: {result.name}")
        self.stdout.write(
            f"  threshold {settings['candidate_threshold']:g}"
            f"   cooldown {settings['cooldown_seconds']}s"
            f"   windows {settings['current_window_seconds']}s/"
            f"{settings['baseline_window_seconds']}s"
            f"   gate {settings['minimum_current_messages']}msg/"
            f"{settings['minimum_current_chatters']}chatters"
        )
        self.stdout.write(
            "  weights   "
            + "  ".join(f"{name} {weight:.2f}" for name, weight in settings["weights"].items())
        )

        self.stdout.write(f"  observations           {metrics.observations:>8}")
        self.stdout.write(f"  threshold crossings    {metrics.threshold_crossings:>8}")
        self.stdout.write(f"  cooldown suppressed    {len(result.replay.suppressed):>8}")
        self.stdout.write(f"  candidates             {metrics.predicted_moments:>8}")

        self.stdout.write(
            f"  true positives         {metrics.true_positives:>8}"
            f"   false positives {metrics.false_positives}"
            f"   false negatives {metrics.false_negatives}"
        )
        self.stdout.write(
            f"  precision {metrics.precision:.3f}"
            f"   recall {metrics.recall:.3f}"
            f"   F1 {metrics.f1:.3f}"
        )
        self.stdout.write(
            f"  timing error           {_seconds(metrics.mean_absolute_timing_error_seconds)} mean"
            f", {_seconds(metrics.median_absolute_timing_error_seconds)} median"
        )
        self.stdout.write(
            f"  candidate rate         {_rate(metrics.candidate_rate_per_hour)}"
            f"   crossings {_rate(metrics.threshold_crossing_rate_per_hour)}"
        )

        for moment in result.replay.candidate_timestamps:
            self.stdout.write(f"    candidate at {moment.isoformat()}")

    def _report_ranking(self, results: list[comparison.ConfigurationResult]) -> None:
        self.stdout.write("")
        self.stdout.write("Ranked by F1 (for reading only):")
        for position, result in enumerate(comparison.rank(results), start=1):
            self.stdout.write(
                f"  {position}. {result.name:<24} F1 {result.metrics.f1:.3f}"
                f"   precision {result.metrics.precision:.3f}"
                f"   recall {result.metrics.recall:.3f}"
                f"   {_rate(result.metrics.candidate_rate_per_hour)}"
            )
        self.stdout.write(
            self.style.WARNING(
                "This ranking selects nothing. Changing the production default needs "
                "evidence from several sessions and different stream conditions, "
                "and an explicit decision."
            )
        )


def _seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}s"


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}/h"
