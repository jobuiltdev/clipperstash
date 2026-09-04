"""Chat normalization, pseudonymization and persistence.

Twitch's `channel.chat.message` payload is turned into a small typed value here
and nowhere else, so domain code never handles raw EventSub JSON. Everything
this module keeps is something the moment detector will actually read.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.monitoring.exceptions import (
    ChatConfigurationError,
    ChatMessageRejected,
    StreamNotLiveError,
)
from apps.monitoring.models import ChatMessage, StreamSession, StreamSessionStatus
from apps.twitch.client import (
    CHAT_MESSAGE_SUBSCRIPTION_TYPE,
    CHAT_MESSAGE_SUBSCRIPTION_VERSION,
)
from apps.twitch.eventsub.messages import NOTIFICATION, EventSubMessage

logger = logging.getLogger(__name__)

EMOTE_FRAGMENT_TYPE = "emote"


@dataclass(frozen=True)
class NormalizedChatMessage:
    """A chat message reduced to what ClipperStash keeps.

    `chatter_user_id` is present only so the caller can hash it immediately; it
    is never persisted and never logged.
    """

    event_message_id: str
    message_id: str
    broadcaster_user_id: str
    chatter_user_id: str = field(repr=False)
    text: str = field(repr=False)
    timestamp: datetime
    emote_count: int


def hash_chatter_id(chatter_user_id: str) -> str:
    """Pseudonymize a chatter's Twitch user id.

    A keyed HMAC rather than a bare digest: Twitch user ids are short numeric
    strings, so an unkeyed hash would be trivially reversible by enumeration.
    The key lives only in the environment, is never returned by an API and is
    never logged. It is deliberately not `TWITCH_CLIENT_SECRET`, so the two can
    rotate independently.

    The result supports one question only — "how many distinct chatters reacted"
    — and cannot be turned back into a Twitch identity without the key.
    """
    secret = getattr(settings, "CHAT_USER_HASH_SECRET", "")
    if not secret:
        raise ChatConfigurationError(
            "CHAT_USER_HASH_SECRET is not configured; chat cannot be ingested."
        )
    return hmac.new(secret.encode("utf-8"), chatter_user_id.encode("utf-8"), sha256).hexdigest()


def count_emote_fragments(fragments: Any) -> int:
    """Count emote fragments in Twitch's structured message breakdown.

    Emotes are counted from Twitch's own fragments, never by scanning the text
    for colon syntax, which would misread ordinary punctuation. A fragment list
    that is not a list is a malformed payload and rejected; individual entries
    that are not objects are skipped, since one odd fragment should not discard
    an otherwise usable message.
    """
    if fragments is None:
        return 0
    if not isinstance(fragments, list):
        raise ChatMessageRejected("Chat message fragments were not a list.")

    count = 0
    for fragment in fragments:
        if not isinstance(fragment, dict):
            continue
        if fragment.get("type") == EMOTE_FRAGMENT_TYPE:
            count += 1
    return count


def normalize_chat_message(
    message: EventSubMessage,
    *,
    expected_broadcaster_user_id: str,
) -> NormalizedChatMessage:
    """Validate and reduce one `channel.chat.message` notification.

    Raises `ChatMessageRejected` for anything that is not a usable chat
    notification for the expected channel. Rejecting is always preferred to
    guessing: a message we cannot attribute confidently is dropped rather than
    filed against the wrong broadcast.
    """
    if message.message_type != NOTIFICATION:
        raise ChatMessageRejected(f"Not a notification ({message.message_type!r}).")
    if not message.message_id:
        raise ChatMessageRejected("Notification carried no EventSub message id.")

    subscription = message.payload.get("subscription")
    subscription_type = message.subscription_type
    subscription_version = message.subscription_version
    if isinstance(subscription, dict):
        subscription_type = subscription_type or str(subscription.get("type") or "")
        subscription_version = subscription_version or str(subscription.get("version") or "")

    if subscription_type != CHAT_MESSAGE_SUBSCRIPTION_TYPE:
        raise ChatMessageRejected(f"Unexpected subscription type {subscription_type!r}.")
    if subscription_version != CHAT_MESSAGE_SUBSCRIPTION_VERSION:
        raise ChatMessageRejected(f"Unexpected subscription version {subscription_version!r}.")

    event = message.payload.get("event")
    if not isinstance(event, dict):
        raise ChatMessageRejected("Notification carried no event object.")

    broadcaster_user_id = str(event.get("broadcaster_user_id") or "")
    if not broadcaster_user_id:
        raise ChatMessageRejected("Chat event carried no broadcaster id.")
    # Shared Chat can surface a source broadcaster; it is ignored on purpose.
    # The subscription's target channel is the ownership boundary, so a message
    # can never be filed against a session it does not belong to.
    if broadcaster_user_id != expected_broadcaster_user_id:
        raise ChatMessageRejected("Chat event was for a different broadcaster.")

    message_id = str(event.get("message_id") or "")
    if not message_id:
        raise ChatMessageRejected("Chat event carried no message id.")

    chatter_user_id = str(event.get("chatter_user_id") or "")
    if not chatter_user_id:
        raise ChatMessageRejected("Chat event carried no chatter id.")

    body = event.get("message")
    if not isinstance(body, dict):
        raise ChatMessageRejected("Chat event carried no message object.")

    text = body.get("text")
    if not isinstance(text, str):
        raise ChatMessageRejected("Chat event carried no message text.")

    emote_count = count_emote_fragments(body.get("fragments"))

    # Twitch's EventSub `message_timestamp` is when the notification was
    # produced, which is the closest authoritative delivery time available for
    # this subscription type; `channel.chat.message` carries no separate
    # sent-at field. It is always timezone-aware UTC, and the local clock is
    # never substituted for it.
    if message.message_timestamp is None:
        raise ChatMessageRejected("Notification carried no usable timestamp.")

    return NormalizedChatMessage(
        event_message_id=message.message_id,
        message_id=message_id,
        broadcaster_user_id=broadcaster_user_id,
        chatter_user_id=chatter_user_id,
        text=text,
        timestamp=message.message_timestamp,
        emote_count=emote_count,
    )


def store_chat_message(
    session: StreamSession,
    normalized: NormalizedChatMessage,
) -> ChatMessage | None:
    """Persist one normalized message against a live session.

    Returns None when the message was a duplicate. EventSub delivery is
    at-least-once, so the same notification can arrive more than once; the
    database's unique columns — not an in-memory set — are what make that
    harmless, which also holds across restarts and processes.
    """
    if session.status != StreamSessionStatus.LIVE:
        raise StreamNotLiveError()

    try:
        with transaction.atomic():
            return ChatMessage.objects.create(
                session=session,
                twitch_event_message_id=normalized.event_message_id,
                twitch_message_id=normalized.message_id,
                chatter_user_id_hash=hash_chatter_id(normalized.chatter_user_id),
                text=normalized.text,
                timestamp=normalized.timestamp,
                emote_count=normalized.emote_count,
            )
    except IntegrityError:
        # Either the same EventSub notification or the same chat message has
        # already been recorded. Both are expected and neither is an error.
        logger.debug("Ignoring a duplicate chat message delivery.")
        return None
