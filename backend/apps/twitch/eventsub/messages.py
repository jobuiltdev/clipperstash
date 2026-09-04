"""Parsing of the EventSub WebSocket envelope.

This module knows Twitch's transport format and nothing about ClipperStash's
domain. It turns a raw frame into a typed message, or reports that the frame was
unusable. It never raises on an unknown message type: Twitch may add types, and
an unrecognized one must not stop a running monitor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.utils.dateparse import parse_datetime

# Message types Twitch documents for the WebSocket transport.
SESSION_WELCOME = "session_welcome"
SESSION_KEEPALIVE = "session_keepalive"
NOTIFICATION = "notification"
SESSION_RECONNECT = "session_reconnect"
REVOCATION = "revocation"


class EventSubProtocolError(Exception):
    """A frame did not match Twitch's documented envelope."""


@dataclass(frozen=True)
class EventSubMessage:
    """The common envelope around every EventSub WebSocket frame."""

    message_id: str
    message_type: str
    message_timestamp: datetime | None
    payload: dict[str, Any] = field(repr=False, default_factory=dict)
    subscription_type: str = ""
    subscription_version: str = ""


@dataclass(frozen=True)
class SessionInfo:
    """The `session` object carried by welcome and reconnect messages."""

    session_id: str
    keepalive_timeout_seconds: int | None
    reconnect_url: str | None
    status: str


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = parse_datetime(value)
    except ValueError:
        return None
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed


def parse_message(raw: str | bytes) -> EventSubMessage:
    """Parse one WebSocket frame into a typed envelope.

    Raises `EventSubProtocolError` when the frame is not JSON, is not an object,
    or carries no usable `metadata.message_type`. An unfamiliar *type* is not an
    error here — the runtime decides what to do with it.
    """
    if isinstance(raw, bytes | bytearray):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise EventSubProtocolError("EventSub frame was not valid UTF-8.") from None

    try:
        body = json.loads(raw)
    except (TypeError, ValueError):
        raise EventSubProtocolError("EventSub frame was not valid JSON.") from None

    if not isinstance(body, dict):
        raise EventSubProtocolError("EventSub frame was not a JSON object.")

    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        raise EventSubProtocolError("EventSub frame carried no metadata object.")

    message_type = metadata.get("message_type")
    if not isinstance(message_type, str) or not message_type:
        raise EventSubProtocolError("EventSub frame carried no message type.")

    payload = body.get("payload")
    if payload is not None and not isinstance(payload, dict):
        raise EventSubProtocolError("EventSub frame carried a non-object payload.")

    return EventSubMessage(
        message_id=str(metadata.get("message_id") or ""),
        message_type=message_type,
        message_timestamp=_timestamp(metadata.get("message_timestamp")),
        payload=payload or {},
        subscription_type=str(metadata.get("subscription_type") or ""),
        subscription_version=str(metadata.get("subscription_version") or ""),
    )


def session_info(message: EventSubMessage) -> SessionInfo:
    """Read the session object from a welcome or reconnect message."""
    session = message.payload.get("session")
    if not isinstance(session, dict):
        raise EventSubProtocolError(f"EventSub {message.message_type} carried no session object.")

    session_id = str(session.get("id") or "")
    if not session_id:
        raise EventSubProtocolError(f"EventSub {message.message_type} carried no session id.")

    keepalive = session.get("keepalive_timeout_seconds")
    if isinstance(keepalive, bool) or not isinstance(keepalive, int) or keepalive <= 0:
        keepalive = None

    reconnect_url = session.get("reconnect_url")
    if not isinstance(reconnect_url, str) or not reconnect_url:
        reconnect_url = None

    return SessionInfo(
        session_id=session_id,
        keepalive_timeout_seconds=keepalive,
        reconnect_url=reconnect_url,
        status=str(session.get("status") or ""),
    )
