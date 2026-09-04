"""The WebSocket socket itself.

A thin seam over `websockets`' synchronous client, so the runtime can be driven
by a fake socket in tests without any network. Nothing here knows about chat.
"""

from __future__ import annotations

import logging
from typing import Protocol

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect as websocket_connect

logger = logging.getLogger(__name__)

# How long a single `recv` may block beyond the negotiated keepalive before the
# connection is treated as dead.
KEEPALIVE_GRACE_SECONDS = 5.0

CONNECT_TIMEOUT_SECONDS = 10.0
CLOSE_TIMEOUT_SECONDS = 5.0

# Twitch frames are small; a generous ceiling still refuses anything absurd.
MAX_FRAME_BYTES = 1_048_576


class EventSubConnectionError(Exception):
    """The WebSocket could not be opened, or died while being read."""


class EventSubSocket(Protocol):
    """The surface the runtime needs from a socket."""

    def recv(self, timeout: float | None = None) -> str | bytes: ...

    def close(self) -> None: ...


class WebSocketAdapter:
    """Adapts a `websockets` sync connection to `EventSocket`."""

    def __init__(self, connection) -> None:
        self._connection = connection

    def recv(self, timeout: float | None = None) -> str | bytes:
        try:
            return self._connection.recv(timeout=timeout)
        except TimeoutError:
            raise
        except WebSocketException as exc:
            raise EventSubConnectionError(
                f"EventSub socket failed ({type(exc).__name__})."
            ) from None
        except OSError as exc:
            raise EventSubConnectionError(
                f"EventSub socket failed ({type(exc).__name__})."
            ) from None

    def close(self) -> None:
        try:
            self._connection.close()
        except Exception:  # noqa: BLE001 - closing must never raise into the runtime
            logger.debug("Ignoring error while closing an EventSub socket.")


def open_socket(url: str) -> EventSubSocket:
    """Open an EventSub WebSocket.

    `ping_interval=None` stops the library initiating its own pings: Twitch's
    EventSub socket is receive-oriented and ClipperStash sends it nothing. The
    library still answers Twitch's pings with protocol-level pongs, which is the
    only traffic that ever leaves this side.
    """
    try:
        connection = websocket_connect(
            url,
            open_timeout=CONNECT_TIMEOUT_SECONDS,
            close_timeout=CLOSE_TIMEOUT_SECONDS,
            ping_interval=None,
            max_size=MAX_FRAME_BYTES,
        )
    except (WebSocketException, OSError, TimeoutError) as exc:
        raise EventSubConnectionError(
            f"Could not open the EventSub socket ({type(exc).__name__})."
        ) from None
    return WebSocketAdapter(connection)
