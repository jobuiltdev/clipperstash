"""Wiring between the EventSub runtime and chat persistence.

`ChatMonitor` is the only place the transport layer and the chat domain meet. It
checks preconditions first, then hands the runtime three narrow callbacks: create
the subscription for a session id, store a notification, and say whether the run
should continue.

That last one keeps chat subordinate to stream lifecycle. A run follows one
broadcast, so when stream observation ends that `StreamSession`, the monitor
stops and closes its socket rather than sitting connected and discarding
messages. Chat never ends a session itself — it only notices that Milestone 3
already did.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from apps.monitoring.chat import hash_chatter_id, normalize_chat_message, store_chat_message
from apps.monitoring.exceptions import (
    ChatAuthorizationError,
    ChatMessageRejected,
    StreamNotLiveError,
)
from apps.monitoring.models import StreamSession, StreamSessionStatus
from apps.twitch import services as twitch_services
from apps.twitch.client import TwitchClient
from apps.twitch.eventsub.client import open_socket
from apps.twitch.eventsub.messages import EventSubMessage
from apps.twitch.eventsub.runtime import EventSubRevoked, EventSubRuntime, RuntimeStats
from apps.twitch.exceptions import TwitchAuthenticationError
from apps.twitch.models import TwitchConnection

logger = logging.getLogger(__name__)


@dataclass
class ChatMonitorStats:
    """What a monitoring run did, in terms safe to print or log."""

    runtime: RuntimeStats
    stored: int = 0
    duplicates: int = 0
    rejected: int = 0
    revoked_reason: str | None = None
    # A normal stop, not a failure: stream observation ended the broadcast this
    # run was following.
    stopped_because_session_ended: bool = False


def resolve_target_session(streamer_id: int | None, session_id: int | None) -> StreamSession:
    """Find the live session chat should attach to.

    Chat never creates a session. If the streamer is not live according to
    stream observation, monitoring refuses to start rather than inventing one.
    """
    if session_id is not None:
        session = StreamSession.objects.filter(pk=session_id).select_related("streamer").first()
        if session is None:
            raise StreamNotLiveError("No stream session with that id exists.")
    else:
        session = (
            StreamSession.objects.filter(streamer_id=streamer_id, status=StreamSessionStatus.LIVE)
            .select_related("streamer")
            .first()
        )
        if session is None:
            raise StreamNotLiveError()

    if session.status != StreamSessionStatus.LIVE:
        raise StreamNotLiveError("That stream session has already ended.")
    return session


def require_chat_connection() -> TwitchConnection:
    """The connected Twitch account, if it may read chat.

    Refuses before any socket is opened when the connection predates the
    `user:read:chat` scope, so an unusable run is never started.
    """
    connection = TwitchConnection.objects.current()
    if connection is None:
        raise ChatAuthorizationError("No Twitch account is connected.")
    if not connection.can_read_chat:
        raise ChatAuthorizationError()
    return connection


class ChatMonitor:
    """Ingests one channel's chat for the life of one live session."""

    def __init__(
        self,
        session: StreamSession,
        connection: TwitchConnection,
        *,
        client: TwitchClient | None = None,
        socket_factory: Callable[[str], object] | None = None,
        max_connections: int | None = None,
        should_continue: Callable[[], bool] = lambda: True,
    ) -> None:
        self.session = session
        self.connection = connection
        self._client = client
        self._socket_factory = socket_factory
        self._max_connections = max_connections
        self._caller_should_continue = should_continue
        self.stats = ChatMonitorStats(runtime=RuntimeStats())

    def run(self) -> ChatMonitorStats:
        """Open the socket, subscribe and ingest until stopped or revoked."""
        # Fail before touching the network if hashing is unconfigured, so a run
        # cannot start only to drop every message it receives.
        self._require_hashing_secret()

        runtime = EventSubRuntime(
            subscribe=self._subscribe,
            handle_notification=self._handle_notification,
            # Resolved here rather than defaulted at import time, so the
            # transport is always an explicit choice of this call.
            socket_factory=self._socket_factory or open_socket,
            max_connections=self._max_connections,
            should_continue=self._should_continue,
        )

        try:
            self.stats.runtime = runtime.run()
        except EventSubRevoked as exc:
            # Losing chat authorization says nothing about the broadcast. The
            # session is left exactly as stream observation left it, and stored
            # history is untouched.
            self.stats.runtime = runtime.stats
            self.stats.revoked_reason = exc.reason
            self._on_revoked()
        except TwitchAuthenticationError:
            self.stats.runtime = runtime.stats
            logger.warning("Chat monitoring stopped: Twitch would not authorize the subscription.")
            raise

        return self.stats

    # -- callbacks ------------------------------------------------------------

    def _subscribe(self, session_id: str) -> None:
        twitch_services.create_chat_message_subscription(
            self.connection,
            broadcaster_user_id=self.session.streamer.platform_user_id,
            session_id=session_id,
            client=self._client,
        )

    def _should_continue(self) -> bool:
        """Whether this run should keep reading.

        Two independent reasons to stop: the caller asked (Ctrl+C), or stream
        observation has ended the broadcast. The runtime consults this before
        every receive and before replacing a socket, so a quiet channel sending
        only keepalives still notices promptly.
        """
        return self._caller_should_continue() and self._session_is_live()

    def _session_is_live(self) -> bool:
        """Re-read only the session's status from the database.

        The in-memory `StreamSession` loaded at startup would never notice the
        broadcast ending, so the current value is read each time. `exists()` on
        the primary key is a single indexed lookup that fetches no columns and
        touches no chat history.

        Milestone 3 remains the sole authority on stream lifecycle: this only
        observes what it has already decided.
        """
        still_live = StreamSession.objects.filter(
            pk=self.session.pk, status=StreamSessionStatus.LIVE
        ).exists()

        if not still_live and not self.stats.stopped_because_session_ended:
            self.stats.stopped_because_session_ended = True
            logger.info(
                "Stream session %s is no longer live; stopping chat monitoring.",
                self.session.pk,
            )
        return still_live

    def _handle_notification(self, message: EventSubMessage) -> None:
        try:
            normalized = normalize_chat_message(
                message,
                expected_broadcaster_user_id=self.session.streamer.platform_user_id,
            )
        except ChatMessageRejected as exc:
            # Log the reason, never the payload or the text.
            self.stats.rejected += 1
            logger.warning("Dropped a chat notification: %s", exc)
            return

        try:
            stored = store_chat_message(self.session, normalized)
        except StreamNotLiveError:
            self.stats.rejected += 1
            logger.info("Session %s is no longer live; stopping chat persistence.", self.session.pk)
            return

        if stored is None:
            self.stats.duplicates += 1
        else:
            self.stats.stored += 1

    # -- helpers --------------------------------------------------------------

    def _require_hashing_secret(self) -> None:
        """Raise `ChatConfigurationError` now rather than per message later."""
        hash_chatter_id("preflight")

    def _on_revoked(self) -> None:
        """Record that authorization is gone, without touching stream state."""
        logger.warning(
            "Chat subscription revoked for session %s; marking the connection "
            "as needing re-authorization.",
            self.session.pk,
        )
        self.connection.mark_requires_reauthorization()
