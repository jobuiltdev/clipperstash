"""Create a Twitch clip for one detected moment.

Explicit and manual. Nothing invokes this automatically: chat ingestion does not
call the detector, and the detector does not call this. Milestone 6 proves the
clipping transaction on its own.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.clips import services
from apps.clips.config import DEFAULT_CONFIG
from apps.clips.exceptions import (
    ClipPreconditionError,
    ClipRequestFailed,
    ClipRequestStateUnknown,
)
from apps.moments.models import MomentCandidate


class Command(BaseCommand):
    help = (
        "Request a Twitch clip for a detected moment and wait for Twitch to confirm "
        "it. The moment must be recent and its stream still live. Runs once and exits."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("candidate_id", type=int, help="Moment candidate id to clip.")
        parser.add_argument(
            "--verify-only",
            action="store_true",
            help=(
                "Do not send a new request; only confirm a clip Twitch has already "
                "accepted. Use this to resume an interrupted run."
            ),
        )

    def handle(self, *args, **options) -> None:
        candidate = (
            MomentCandidate.objects.filter(pk=options["candidate_id"])
            .select_related("session__streamer")
            .first()
        )
        if candidate is None:
            raise CommandError(f"No moment candidate with id {options['candidate_id']}.")

        self._report_candidate(candidate)

        try:
            result = services.request_clip(candidate, verify_only=options["verify_only"])
        except ClipRequestStateUnknown as exc:
            # Deliberately does not say the clip does not exist: that is exactly
            # what is unknown.
            self.stdout.write(
                self.style.WARNING(
                    "Clip request state is unknown; no automatic retry was attempted "
                    "because Twitch may already have received the original request."
                )
            )
            raise CommandError(f"{exc.code}: {exc}") from None
        except ClipPreconditionError as exc:
            # Nothing was claimed and nothing was sent; the candidate is untouched.
            raise CommandError(f"{exc.code}: {exc}") from None
        except ClipRequestFailed as exc:
            candidate.refresh_from_db()
            raise CommandError(
                f"{exc.code}: {exc} (moment {candidate.pk} is now {candidate.status})"
            ) from None

        self._report_success(result)

    # -- output ---------------------------------------------------------------

    def _report_candidate(self, candidate: MomentCandidate) -> None:
        age = services.recent_candidate_age(candidate).total_seconds()
        self.stdout.write(
            f"Moment {candidate.pk} — session {candidate.session_id} "
            f"({candidate.session.streamer.username})"
        )
        self.stdout.write(f"  score      {candidate.total_score:.2f} / 100")
        self.stdout.write(f"  detected   {candidate.detected_at.isoformat()} ({age:.1f}s ago)")
        self.stdout.write(f"  status     {candidate.status}")
        self.stdout.write(
            f"  freshness  must be at most {DEFAULT_CONFIG.max_candidate_age_seconds:.0f}s old"
        )
        self.stdout.write("")

    def _report_success(self, result: services.ClipResult) -> None:
        clip = result.clip

        if result.already_ready:
            action = "already confirmed"
        elif result.resumed:
            action = "resumed an existing request"
        else:
            action = "requested"

        self.stdout.write(f"Clip {action}.")
        self.stdout.write(f"  twitch clip id  {clip.twitch_clip_id}")
        if result.attempts:
            self.stdout.write(f"  verified after  {result.attempts} check(s)")
        self.stdout.write(f"  title           {clip.title or '(untitled)'}")
        if clip.duration is not None:
            self.stdout.write(f"  duration        {clip.duration:.1f}s")
        self.stdout.write(f"  url             {clip.twitch_url}")
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(f"Moment {result.candidate.pk} is now {result.candidate.status}.")
        )
