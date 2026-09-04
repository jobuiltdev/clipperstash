"""Run chat ingestion for one live stream session.

Deliberately a foreground command rather than a scheduled job. V0 proves the
ingestion path for a single channel; how monitoring should be driven
continuously is a decision for a later milestone, once the shape of the moment
detector is known. Nothing starts this automatically — not `runserver`, not
Celery, not a timer.
"""

from __future__ import annotations

import logging
import signal
from types import FrameType

from django.core.management.base import BaseCommand, CommandError

from apps.monitoring.chat_monitor import (
    ChatMonitor,
    require_chat_connection,
    resolve_target_session,
)
from apps.monitoring.exceptions import MonitoringError
from apps.streamers.models import Streamer
from apps.twitch.exceptions import TwitchError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Ingest Twitch chat for a live stream session over an EventSub WebSocket. "
        "Requires a connected Twitch account with the user:read:chat scope and a "
        "stream session that stream observation has already recorded as live. "
        "Runs until interrupted, or until that session is no longer live."
    )

    def add_arguments(self, parser) -> None:
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument(
            "streamer_id",
            nargs="?",
            type=int,
            help="Resolved streamer id; uses that streamer's live session.",
        )
        target.add_argument(
            "--session",
            dest="session_id",
            type=int,
            help="Stream session id to attach chat to, instead of a streamer id.",
        )
        parser.add_argument(
            "--max-connections",
            type=int,
            default=None,
            help="Stop after this many EventSub connections. Useful for a bounded run.",
        )

    def handle(self, *args, **options) -> None:
        streamer_id = options.get("streamer_id")
        session_id = options.get("session_id")

        if streamer_id is not None and not Streamer.objects.filter(pk=streamer_id).exists():
            raise CommandError(f"No resolved streamer with id {streamer_id}.")

        try:
            session = resolve_target_session(streamer_id, session_id)
            connection = require_chat_connection()
        except MonitoringError as exc:
            raise CommandError(f"{exc.code}: {exc}") from None

        stopping = False

        def should_continue() -> bool:
            return not stopping

        def stop(_signum: int, _frame: FrameType | None) -> None:
            nonlocal stopping
            stopping = True
            self.stdout.write(self.style.WARNING("Stopping after the current message…"))

        previous = signal.signal(signal.SIGINT, stop)

        self.stdout.write(
            f"Monitoring chat for {session.streamer.username} "
            f"(session {session.pk}). Stops when the session ends, or on Ctrl+C."
        )

        monitor = ChatMonitor(
            session,
            connection,
            max_connections=options.get("max_connections"),
            should_continue=should_continue,
        )

        try:
            stats = monitor.run()
        except MonitoringError as exc:
            raise CommandError(f"{exc.code}: {exc}") from None
        except TwitchError as exc:
            # Typed integration errors carry no secrets, so the text is safe.
            raise CommandError(f"Twitch would not allow chat monitoring: {exc}") from None
        finally:
            signal.signal(signal.SIGINT, previous)

        if stats.stopped_because_session_ended:
            self.stdout.write(
                self.style.SUCCESS("The stream session ended; chat monitoring stopped.")
            )

        if stats.revoked_reason:
            self.stdout.write(
                self.style.WARNING(
                    f"Twitch revoked the subscription ({stats.revoked_reason}). "
                    "Reconnect the Twitch account to resume."
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Stored {stats.stored} message(s); {stats.duplicates} duplicate(s), "
                f"{stats.rejected} rejected, across {stats.runtime.connections} connection(s)."
            )
        )
