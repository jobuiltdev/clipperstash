"""Shared builders for EventSub chat tests.

Nothing here touches a network. WebSocket behavior is supplied by `FakeSocket`,
which replays a scripted list of frames, and Twitch HTTP by the existing
`httpx.MockTransport` helpers.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from apps.twitch.client import HELIX_BASE_URL, OAUTH_TOKEN_URL
from apps.twitch.eventsub.client import EventSubConnectionError
from apps.twitch.tests.conftest import (
    FAKE_APP_TOKEN,
    RecordingTransport,
    json_response,
    routed_transport,
    token_payload,
)

HELIX_EVENTSUB_URL = f"{HELIX_BASE_URL}/eventsub/subscriptions"

BROADCASTER_ID = "37402112"
CONNECTED_USER_ID = "123456"
SESSION_ID = "AQoQexAWVYKSTIu4ec_2VAxyuhAB"
RECONNECT_URL = "wss://eventsub.wss.twitch.tv/ws?challenge=abc&id=xyz"
EVENT_TIMESTAMP = "2026-09-04T12:00:00.123456789Z"


# -- WebSocket frames ---------------------------------------------------------


def _envelope(message_type: str, payload: dict[str, Any], **metadata: Any) -> str:
    return json.dumps(
        {
            "metadata": {
                "message_id": metadata.pop("message_id", "envelope-1"),
                "message_type": message_type,
                "message_timestamp": metadata.pop("message_timestamp", EVENT_TIMESTAMP),
                **metadata,
            },
            "payload": payload,
        }
    )


def welcome_frame(
    *,
    session_id: str = SESSION_ID,
    keepalive_timeout_seconds: int | None = 10,
    message_id: str = "welcome-1",
) -> str:
    session: dict[str, Any] = {
        "id": session_id,
        "status": "connected",
        "connected_at": EVENT_TIMESTAMP,
        "reconnect_url": None,
    }
    if keepalive_timeout_seconds is not None:
        session["keepalive_timeout_seconds"] = keepalive_timeout_seconds
    return _envelope("session_welcome", {"session": session}, message_id=message_id)


def keepalive_frame(message_id: str = "keepalive-1") -> str:
    return _envelope("session_keepalive", {}, message_id=message_id)


def reconnect_frame(*, reconnect_url: str | None = RECONNECT_URL) -> str:
    return _envelope(
        "session_reconnect",
        {
            "session": {
                "id": SESSION_ID,
                "status": "reconnecting",
                "keepalive_timeout_seconds": None,
                "reconnect_url": reconnect_url,
                "connected_at": EVENT_TIMESTAMP,
            }
        },
        message_id="reconnect-1",
    )


def revocation_frame(status: str = "authorization_revoked") -> str:
    return _envelope(
        "revocation",
        {
            "subscription": {
                "id": "sub-1",
                "type": "channel.chat.message",
                "version": "1",
                "status": status,
            }
        },
        message_id="revocation-1",
    )


def chat_event(
    *,
    message_id: str = "chat-1",
    broadcaster_user_id: str = BROADCASTER_ID,
    chatter_user_id: str = "900001",
    text: str = "that was insane",
    fragments: Any = None,
) -> dict[str, Any]:
    """One `channel.chat.message` event, including fields ClipperStash drops."""
    if fragments is None:
        fragments = [{"type": "text", "text": text}]
    return {
        "broadcaster_user_id": broadcaster_user_id,
        "broadcaster_user_login": "shroud",
        "broadcaster_user_name": "shroud",
        "chatter_user_id": chatter_user_id,
        "chatter_user_login": "viewer",
        "chatter_user_name": "Viewer",
        "message_id": message_id,
        "message": {"text": text, "fragments": fragments},
        "color": "#1E90FF",
        "badges": [{"set_id": "subscriber", "id": "12", "info": "16"}],
        "message_type": "text",
        "cheer": None,
        "reply": None,
        "channel_points_custom_reward_id": None,
    }


def notification_frame(
    *,
    event: dict[str, Any] | None = None,
    envelope_message_id: str = "notify-1",
    subscription_type: str = "channel.chat.message",
    subscription_version: str = "1",
    message_timestamp: str = EVENT_TIMESTAMP,
) -> str:
    return _envelope(
        "notification",
        {
            "subscription": {
                "id": "sub-1",
                "type": subscription_type,
                "version": subscription_version,
                "status": "enabled",
                "condition": {
                    "broadcaster_user_id": BROADCASTER_ID,
                    "user_id": CONNECTED_USER_ID,
                },
            },
            "event": event if event is not None else chat_event(),
        },
        message_id=envelope_message_id,
        message_timestamp=message_timestamp,
        subscription_type=subscription_type,
        subscription_version=subscription_version,
    )


def emote_fragments(count: int, *, text: str = "hype") -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for index in range(count):
        fragments.append(
            {
                "type": "emote",
                "text": f"Emote{index}",
                "emote": {"id": str(index), "emote_set_id": "0"},
            }
        )
    return fragments


# -- fake socket --------------------------------------------------------------


class FakeSocket:
    """Replays scripted frames, then behaves as instructed.

    A frame may be a string (delivered), `TimeoutError` (silence past the
    keepalive window), or `EventSubConnectionError` (the socket dying).
    """

    def __init__(self, frames: Iterable[Any], *, url: str = "") -> None:
        self._frames = list(frames)
        self.url = url
        self.closed = False
        self.sent: list[Any] = []
        self.recv_timeouts: list[float | None] = []

    def recv(self, timeout: float | None = None) -> str:
        self.recv_timeouts.append(timeout)
        if not self._frames:
            raise TimeoutError
        frame = self._frames.pop(0)
        if isinstance(frame, type) and issubclass(frame, BaseException):
            raise frame
        if isinstance(frame, BaseException):
            raise frame
        return frame

    def close(self) -> None:
        self.closed = True

    # Present only so a test can prove nothing ever calls it.
    def send(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - must not run
        self.sent.append((args, kwargs))
        raise AssertionError("ClipperStash must not send anything to the EventSub socket.")


class ScriptedSocketFactory:
    """Hands out a prepared socket per connection attempt, recording URLs."""

    def __init__(self, scripts: Iterable[Iterable[Any]]) -> None:
        self._scripts = [list(script) for script in scripts]
        self.urls: list[str] = []
        self.sockets: list[FakeSocket] = []

    def __call__(self, url: str) -> FakeSocket:
        self.urls.append(url)
        if not self._scripts:
            raise EventSubConnectionError("No further connections were scripted.")
        socket = FakeSocket(self._scripts.pop(0), url=url)
        self.sockets.append(socket)
        return socket


# -- Twitch HTTP --------------------------------------------------------------


def subscription_payload(status: str = "enabled") -> dict[str, Any]:
    return {
        "data": [
            {
                "id": "sub-1",
                "status": status,
                "type": "channel.chat.message",
                "version": "1",
                "condition": {
                    "broadcaster_user_id": BROADCASTER_ID,
                    "user_id": CONNECTED_USER_ID,
                },
                "transport": {"method": "websocket", "session_id": SESSION_ID},
            }
        ]
    }


def subscription_transport(*responses: Any) -> RecordingTransport:
    """A transport answering the subscription POST, and the app-token route.

    The app-token route is present only so a test can assert it is *never*
    called: subscriptions must use the connected user's token.
    """
    if not responses:
        responses = (json_response(202, subscription_payload()),)
    return routed_transport(
        {
            HELIX_EVENTSUB_URL: list(responses) if len(responses) > 1 else responses[0],
            OAUTH_TOKEN_URL: json_response(
                200, token_payload(access_token=FAKE_APP_TOKEN, refresh_token=None)
            ),
        }
    )
