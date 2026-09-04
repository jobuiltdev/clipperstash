"""The EventSub WebSocket lifecycle.

Domain-agnostic: the caller supplies a callback that creates whatever
subscription it wants once a session id exists, and a callback that handles
notifications. This module owns only the socket's lifecycle — welcome,
keepalive, reconnect handoff, revocation and recovery.

Two kinds of reconnect are deliberately distinguished:

* **Twitch-initiated handoff.** A `session_reconnect` message names a
  `reconnect_url`. Twitch carries the existing subscriptions across to that
  replacement connection, so subscriptions are *not* recreated. The old socket
  is held open until the replacement has delivered its own welcome, and only
  then retired.
* **Ordinary loss.** The socket dies, or falls silent past the keepalive
  timeout. There is no handoff, so a fresh connection to the base URL is opened
  and the subscription is recreated against the new session id.

Lost events are not recovered. Twitch does not replay chat messages missed while
a connection was down, and ClipperStash does not invent them: a gap in the
stored messages is a real gap.

The caller may also supply `should_continue`, which is consulted before every
receive and before replacing a socket. It is how an owner ends a run — because
the work it was reading for is over, for instance — without that stop being
confused with a failure.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from apps.twitch.client import EVENTSUB_WEBSOCKET_URL
from apps.twitch.eventsub.client import (
    KEEPALIVE_GRACE_SECONDS,
    EventSubConnectionError,
    EventSubSocket,
    open_socket,
)
from apps.twitch.eventsub.messages import (
    NOTIFICATION,
    REVOCATION,
    SESSION_KEEPALIVE,
    SESSION_RECONNECT,
    SESSION_WELCOME,
    EventSubMessage,
    EventSubProtocolError,
    SessionInfo,
    parse_message,
    session_info,
)

logger = logging.getLogger(__name__)

DEFAULT_KEEPALIVE_SECONDS = 10

SocketFactory = Callable[[str], EventSubSocket]


class EventSubRevoked(Exception):
    """Twitch revoked the subscription; the runtime stopped."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Twitch revoked the EventSub subscription ({reason}).")
        self.reason = reason


class _Outcome(Enum):
    KEEP_READING = "keep_reading"
    HANDOFF = "handoff"
    RECONNECT = "reconnect"
    STOP = "stop"


@dataclass
class RuntimeStats:
    """A small operational summary, safe to log."""

    connections: int = 0
    handoffs: int = 0
    reconnects: int = 0
    notifications: int = 0
    keepalives: int = 0
    unknown_messages: int = 0
    protocol_errors: int = 0
    subscriptions_created: int = 0
    session_ids: list[str] = field(default_factory=list)


class EventSubRuntime:
    """Runs one EventSub WebSocket session for as long as it is asked to."""

    def __init__(
        self,
        *,
        subscribe: Callable[[str], None],
        handle_notification: Callable[[EventSubMessage], None],
        socket_factory: SocketFactory = open_socket,
        base_url: str = EVENTSUB_WEBSOCKET_URL,
        max_connections: int | None = None,
        should_continue: Callable[[], bool] = lambda: True,
    ) -> None:
        self._subscribe = subscribe
        self._handle_notification = handle_notification
        self._open = socket_factory
        # Only ever the official Twitch endpoint, or a reconnect_url Twitch sent
        # over an already-established socket. Never anything user-supplied.
        self._base_url = base_url
        self._max_connections = max_connections
        self._should_continue = should_continue
        self.stats = RuntimeStats()

    # -- public ---------------------------------------------------------------

    def run(self) -> RuntimeStats:
        """Connect, subscribe and process until told to stop, or until revoked."""
        socket: EventSubSocket | None = None
        welcome: SessionInfo | None = None

        try:
            socket, welcome = self._connect(self._base_url, subscribe=True)

            while socket is not None and self._should_continue():
                outcome, handoff_url = self._pump(socket, welcome)

                if outcome is _Outcome.STOP:
                    break

                # Asked again before replacing the socket: whatever the caller
                # is watching may have changed while this connection was being
                # read, and there is no point opening another one for work that
                # is already over.
                if not self._should_continue():
                    break

                # The budget caps how many sockets a run may open, so it is
                # checked before opening another, never before reading one.
                if self._connection_budget_spent():
                    break

                if outcome is _Outcome.HANDOFF and handoff_url:
                    socket, welcome = self._hand_off(socket, handoff_url)
                    continue

                # Ordinary loss: nothing was carried over, so subscribe again.
                socket.close()
                socket = None
                self.stats.reconnects += 1
                socket, welcome = self._connect(self._base_url, subscribe=True)
        finally:
            if socket is not None:
                socket.close()

        return self.stats

    # -- internals ------------------------------------------------------------

    def _connection_budget_spent(self) -> bool:
        return self._max_connections is not None and self.stats.connections >= self._max_connections

    def _connect(self, url: str, *, subscribe: bool) -> tuple[EventSubSocket, SessionInfo]:
        socket, welcome = self._open_and_welcome(url)
        if subscribe:
            self._subscribe(welcome.session_id)
            self.stats.subscriptions_created += 1
        return socket, welcome

    def _hand_off(self, old_socket: EventSubSocket, url: str) -> tuple[EventSubSocket, SessionInfo]:
        """Move to Twitch's replacement connection without losing messages.

        The old socket keeps delivering until the replacement has welcomed us,
        which is what makes the handoff seamless. Subscriptions travel with the
        session, so none is recreated.
        """
        try:
            new_socket, new_welcome = self._connect(url, subscribe=False)
        except (EventSubConnectionError, EventSubProtocolError) as exc:
            logger.warning(
                "Reconnect handoff failed (%s); falling back to a fresh connection.", exc
            )
            old_socket.close()
            self.stats.reconnects += 1
            return self._connect(self._base_url, subscribe=True)

        old_socket.close()
        logger.info("Retired the previous EventSub socket after a successful handoff.")
        return new_socket, new_welcome

    def _open_and_welcome(self, url: str) -> tuple[EventSubSocket, SessionInfo]:
        socket = self._open(url)
        try:
            message = parse_message(socket.recv(timeout=_welcome_timeout()))
        except TimeoutError:
            socket.close()
            raise EventSubConnectionError("Twitch sent no welcome message.") from None
        except Exception:
            socket.close()
            raise

        if message.message_type != SESSION_WELCOME:
            socket.close()
            raise EventSubProtocolError(
                f"Expected a welcome message, got {message.message_type!r}."
            )

        welcome = session_info(message)
        self.stats.connections += 1
        self.stats.session_ids.append(welcome.session_id)
        logger.info(
            "EventSub session opened (keepalive=%ss).",
            welcome.keepalive_timeout_seconds or DEFAULT_KEEPALIVE_SECONDS,
        )
        return socket, welcome

    def _pump(self, socket: EventSubSocket, welcome: SessionInfo) -> tuple[_Outcome, str | None]:
        """Read this socket until it must be replaced or the run should end.

        `should_continue` is evaluated before each receive, so a caller's decision
        to stop is noticed after every inbound frame — a keepalive is enough, and
        a quiet channel does not keep the run alive.
        """
        keepalive = welcome.keepalive_timeout_seconds or DEFAULT_KEEPALIVE_SECONDS
        timeout = keepalive + KEEPALIVE_GRACE_SECONDS

        while self._should_continue():
            try:
                raw = socket.recv(timeout=timeout)
            except TimeoutError:
                # Twitch guarantees a keepalive inside the negotiated window, so
                # silence past it means the connection is gone even though the
                # socket may still look open.
                logger.warning(
                    "No EventSub traffic within %ss; treating the connection as lost.",
                    timeout,
                )
                return _Outcome.RECONNECT, None
            except EventSubConnectionError as exc:
                logger.warning("EventSub socket lost: %s", exc)
                return _Outcome.RECONNECT, None

            try:
                message = parse_message(raw)
            except EventSubProtocolError as exc:
                # A frame we cannot read is dropped. It must not stop the run and
                # must never reach persistence.
                self.stats.protocol_errors += 1
                logger.warning("Discarding an unreadable EventSub frame: %s", exc)
                continue

            outcome, url = self._dispatch(message)
            if outcome is not _Outcome.KEEP_READING:
                return outcome, url

        return _Outcome.STOP, None

    def _dispatch(self, message: EventSubMessage) -> tuple[_Outcome, str | None]:
        if message.message_type == SESSION_KEEPALIVE:
            self.stats.keepalives += 1
            return _Outcome.KEEP_READING, None

        if message.message_type == NOTIFICATION:
            self.stats.notifications += 1
            self._handle_notification(message)
            return _Outcome.KEEP_READING, None

        if message.message_type == SESSION_RECONNECT:
            return self._handoff_target(message)

        if message.message_type == REVOCATION:
            reason = _revocation_reason(message)
            logger.warning("EventSub subscription revoked (%s).", reason)
            raise EventSubRevoked(reason)

        if message.message_type == SESSION_WELCOME:
            # A second welcome on a live socket is not part of the protocol.
            logger.warning("Ignoring an unexpected second welcome message.")
            return _Outcome.KEEP_READING, None

        # An unfamiliar type is Twitch's prerogative, not a failure here.
        self.stats.unknown_messages += 1
        logger.info("Ignoring unknown EventSub message type %r.", message.message_type)
        return _Outcome.KEEP_READING, None

    def _handoff_target(self, message: EventSubMessage) -> tuple[_Outcome, str | None]:
        try:
            info = session_info(message)
        except EventSubProtocolError as exc:
            logger.warning("Unreadable reconnect message (%s); reconnecting from scratch.", exc)
            return _Outcome.RECONNECT, None

        if not info.reconnect_url:
            logger.warning("Reconnect message carried no URL; reconnecting from scratch.")
            return _Outcome.RECONNECT, None

        # Used exactly as Twitch supplied it: no rewriting, no added parameters.
        self.stats.handoffs += 1
        logger.info("Twitch asked for a reconnect handoff.")
        return _Outcome.HANDOFF, info.reconnect_url


def _welcome_timeout() -> float:
    return DEFAULT_KEEPALIVE_SECONDS + KEEPALIVE_GRACE_SECONDS


def _revocation_reason(message: EventSubMessage) -> str:
    subscription = message.payload.get("subscription")
    if isinstance(subscription, dict):
        status = subscription.get("status")
        if isinstance(status, str) and status:
            return status
    return "unspecified"
