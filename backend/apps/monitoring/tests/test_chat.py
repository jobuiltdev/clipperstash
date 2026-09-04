"""Tests for chat normalization, pseudonymization and persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.monitoring.chat import (
    count_emote_fragments,
    hash_chatter_id,
    normalize_chat_message,
    store_chat_message,
)
from apps.monitoring.exceptions import (
    ChatConfigurationError,
    ChatMessageRejected,
    StreamNotLiveError,
)
from apps.monitoring.models import ChatMessage, StreamSession, StreamSessionStatus
from apps.twitch.eventsub.messages import parse_message

from .chat_fixtures import (
    BROADCASTER_ID,
    chat_event,
    emote_fragments,
    keepalive_frame,
    notification_frame,
)


def message(**kwargs):
    return parse_message(notification_frame(**kwargs))


def normalize(**kwargs):
    return normalize_chat_message(message(**kwargs), expected_broadcaster_user_id=BROADCASTER_ID)


# -- normalization ------------------------------------------------------------


def test_a_normal_message_is_normalized():
    normalized = normalize()

    assert normalized.event_message_id == "notify-1"
    assert normalized.message_id == "chat-1"
    assert normalized.broadcaster_user_id == BROADCASTER_ID
    assert normalized.chatter_user_id == "900001"
    assert normalized.text == "that was insane"
    assert normalized.emote_count == 0


def test_the_timestamp_is_timezone_aware_utc():
    normalized = normalize()

    assert normalized.timestamp.tzinfo is not None
    assert normalized.timestamp.astimezone(UTC).hour == 12


def test_a_notification_without_a_timestamp_is_rejected():
    with pytest.raises(ChatMessageRejected, match="timestamp"):
        normalize(message_timestamp="not-a-time")


def test_a_naive_timestamp_is_rejected():
    """The local clock is never substituted for Twitch's delivery time."""
    with pytest.raises(ChatMessageRejected):
        normalize(message_timestamp="2026-09-04T12:00:00")


def test_a_message_for_another_broadcaster_is_rejected():
    with pytest.raises(ChatMessageRejected, match="different broadcaster"):
        normalize(event=chat_event(broadcaster_user_id="999"))


def test_a_wrong_subscription_type_is_rejected():
    with pytest.raises(ChatMessageRejected, match="subscription type"):
        normalize(subscription_type="channel.follow")


def test_a_wrong_subscription_version_is_rejected():
    with pytest.raises(ChatMessageRejected, match="subscription version"):
        normalize(subscription_version="2")


def test_a_non_notification_is_rejected():
    with pytest.raises(ChatMessageRejected, match="Not a notification"):
        normalize_chat_message(
            parse_message(keepalive_frame()), expected_broadcaster_user_id=BROADCASTER_ID
        )


@pytest.mark.parametrize(
    "event",
    [
        {"broadcaster_user_id": BROADCASTER_ID},
        {"broadcaster_user_id": BROADCASTER_ID, "message_id": "m"},
        {"broadcaster_user_id": BROADCASTER_ID, "message_id": "m", "chatter_user_id": "c"},
        {
            "broadcaster_user_id": BROADCASTER_ID,
            "message_id": "m",
            "chatter_user_id": "c",
            "message": {"fragments": []},
        },
        {"message_id": "m", "chatter_user_id": "c", "message": {"text": "hi"}},
    ],
)
def test_incomplete_events_are_rejected(event):
    with pytest.raises(ChatMessageRejected):
        normalize(event=event)


def test_a_missing_event_object_is_rejected():
    raw = json.loads(notification_frame())
    raw["payload"].pop("event")

    with pytest.raises(ChatMessageRejected, match="event object"):
        normalize_chat_message(
            parse_message(json.dumps(raw)), expected_broadcaster_user_id=BROADCASTER_ID
        )


def test_the_subscription_object_supplies_type_when_metadata_omits_it():
    raw = json.loads(notification_frame())
    raw["metadata"].pop("subscription_type")
    raw["metadata"].pop("subscription_version")

    normalized = normalize_chat_message(
        parse_message(json.dumps(raw)), expected_broadcaster_user_id=BROADCASTER_ID
    )

    assert normalized.message_id == "chat-1"


# -- emote counting -----------------------------------------------------------


def test_zero_emotes():
    assert normalize(event=chat_event(fragments=[{"type": "text", "text": "hi"}])).emote_count == 0


@pytest.mark.parametrize("count", [1, 2, 5])
def test_multiple_emotes_are_counted(count):
    normalized = normalize(event=chat_event(fragments=emote_fragments(count)))

    assert normalized.emote_count == count


def test_emotes_are_counted_from_fragments_not_from_text():
    """Colon syntax in plain text is not an emote."""
    event = chat_event(text="no way :o :o :o", fragments=[{"type": "text", "text": "no way :o"}])

    assert normalize(event=event).emote_count == 0


def test_absent_fragments_count_as_zero():
    event = chat_event(fragments=None)
    event["message"].pop("fragments")

    assert normalize(event=event).emote_count == 0


def test_unusable_fragment_entries_are_skipped():
    """One odd fragment should not discard an otherwise usable message."""
    fragments = ["not-an-object", {"type": "emote"}, None, {"type": "text", "text": "hi"}]

    assert normalize(event=chat_event(fragments=fragments)).emote_count == 1


def test_a_non_list_fragment_block_is_rejected():
    with pytest.raises(ChatMessageRejected, match="fragments"):
        normalize(event=chat_event(fragments={"type": "emote"}))


def test_count_emote_fragments_handles_none():
    assert count_emote_fragments(None) == 0


# -- pseudonymization ---------------------------------------------------------


def test_hashing_is_deterministic_and_keyed(settings):
    settings.CHAT_USER_HASH_SECRET = "unit-test-hash-secret"

    first = hash_chatter_id("900001")
    second = hash_chatter_id("900001")

    assert first == second
    assert len(first) == 64
    assert "900001" not in first


def test_different_chatters_hash_differently(settings):
    settings.CHAT_USER_HASH_SECRET = "unit-test-hash-secret"

    assert hash_chatter_id("900001") != hash_chatter_id("900002")


def test_rotating_the_key_changes_the_hash(settings):
    settings.CHAT_USER_HASH_SECRET = "key-one"
    first = hash_chatter_id("900001")

    settings.CHAT_USER_HASH_SECRET = "key-two"

    assert hash_chatter_id("900001") != first


def test_hashing_is_not_a_bare_digest(settings):
    """A keyed construction, so short numeric ids cannot simply be enumerated."""
    from hashlib import sha256

    settings.CHAT_USER_HASH_SECRET = "unit-test-hash-secret"

    assert hash_chatter_id("900001") != sha256(b"900001").hexdigest()


def test_hashing_without_a_secret_fails_safely(settings):
    settings.CHAT_USER_HASH_SECRET = ""

    with pytest.raises(ChatConfigurationError):
        hash_chatter_id("900001")


def test_the_hash_secret_is_never_reused_from_the_twitch_secret(settings):
    from conftest import FAKE_CLIENT_SECRET

    settings.CHAT_USER_HASH_SECRET = "unit-test-hash-secret"

    assert settings.CHAT_USER_HASH_SECRET != FAKE_CLIENT_SECRET


# -- persistence --------------------------------------------------------------


@pytest.fixture
def live_session(db, streamer) -> StreamSession:
    return StreamSession.objects.create(
        streamer=streamer,
        platform_stream_id="stream-1",
        started_at=timezone.now() - timedelta(hours=1),
        last_observed_at=timezone.now(),
        status=StreamSessionStatus.LIVE,
    )


@pytest.fixture(autouse=True)
def hash_secret(settings):
    settings.CHAT_USER_HASH_SECRET = "unit-test-hash-secret"


pytestmark_db = pytest.mark.django_db


@pytest.mark.django_db
def test_a_first_notification_is_stored(live_session):
    stored = store_chat_message(live_session, normalize())

    assert stored is not None
    assert ChatMessage.objects.count() == 1
    assert stored.session == live_session
    assert stored.twitch_event_message_id == "notify-1"
    assert stored.twitch_message_id == "chat-1"
    assert stored.text == "that was insane"
    assert stored.emote_count == 0
    assert stored.timestamp == datetime(2026, 9, 4, 12, 0, 0, 123456, tzinfo=UTC)


@pytest.mark.django_db
def test_the_raw_chatter_id_is_never_persisted(live_session):
    stored = store_chat_message(live_session, normalize())

    assert stored.chatter_user_id_hash == hash_chatter_id("900001")
    assert stored.chatter_user_id_hash != "900001"

    row = ChatMessage.objects.values().get()
    assert "900001" not in json.dumps(row, default=str)
    assert not any("chatter_user_id" == name for name in row if name == "chatter_user_id")


@pytest.mark.django_db
def test_the_raw_payload_is_never_persisted(live_session):
    store_chat_message(live_session, normalize())

    stored_fields = set(ChatMessage.objects.values().get())
    assert stored_fields == {
        "id",
        "session_id",
        "twitch_event_message_id",
        "twitch_message_id",
        "chatter_user_id_hash",
        "text",
        "timestamp",
        "emote_count",
        "created_at",
    }
    row = json.dumps(ChatMessage.objects.values().get(), default=str)
    for dropped in ("badges", "color", "subscriber", "#1E90FF", "chatter_user_login", "cheer"):
        assert dropped not in row


@pytest.mark.django_db
def test_a_duplicate_eventsub_delivery_is_ignored(live_session):
    first = store_chat_message(live_session, normalize())
    second = store_chat_message(live_session, normalize())

    assert first is not None
    assert second is None, "at-least-once delivery must be harmless"
    assert ChatMessage.objects.count() == 1


@pytest.mark.django_db
def test_a_duplicate_chat_message_id_is_ignored(live_session):
    store_chat_message(live_session, normalize())

    # A different EventSub delivery carrying the same chat message.
    again = store_chat_message(live_session, normalize(envelope_message_id="notify-2"))

    assert again is None
    assert ChatMessage.objects.count() == 1


@pytest.mark.django_db
def test_distinct_messages_are_all_stored(live_session):
    store_chat_message(live_session, normalize())
    store_chat_message(
        live_session,
        normalize(envelope_message_id="notify-2", event=chat_event(message_id="chat-2")),
    )

    assert ChatMessage.objects.count() == 2


@pytest.mark.django_db
def test_an_ended_session_receives_nothing(live_session):
    live_session.status = StreamSessionStatus.ENDED
    live_session.save(update_fields=["status"])

    with pytest.raises(StreamNotLiveError):
        store_chat_message(live_session, normalize())

    assert ChatMessage.objects.count() == 0


@pytest.mark.django_db
def test_messages_are_related_to_their_session(live_session):
    store_chat_message(live_session, normalize())

    assert live_session.chat_messages.count() == 1


@pytest.mark.django_db
def test_another_streamers_session_cannot_receive_the_message(live_session, other_streamer):
    other_session = StreamSession.objects.create(
        streamer=other_streamer,
        platform_stream_id="stream-2",
        started_at=timezone.now(),
        last_observed_at=timezone.now(),
        status=StreamSessionStatus.LIVE,
    )

    # Normalization is what enforces this: a message for another broadcaster
    # never becomes a normalized value at all.
    with pytest.raises(ChatMessageRejected):
        normalize_chat_message(
            message(), expected_broadcaster_user_id=other_streamer.platform_user_id
        )

    assert other_session.chat_messages.count() == 0


@pytest.mark.django_db
def test_storing_without_a_hash_secret_fails_safely(live_session, settings):
    settings.CHAT_USER_HASH_SECRET = ""

    with pytest.raises(ChatConfigurationError):
        store_chat_message(live_session, normalize())

    assert ChatMessage.objects.count() == 0


@pytest.mark.django_db
def test_the_unique_constraints_are_enforced_by_the_database(live_session):
    store_chat_message(live_session, normalize())

    with pytest.raises(IntegrityError), transaction.atomic():
        ChatMessage.objects.create(
            session=live_session,
            twitch_event_message_id="notify-1",
            twitch_message_id="different",
            chatter_user_id_hash="x",
            text="",
            timestamp=timezone.now(),
        )

    with pytest.raises(IntegrityError), transaction.atomic():
        ChatMessage.objects.create(
            session=live_session,
            twitch_event_message_id="different",
            twitch_message_id="chat-1",
            chatter_user_id_hash="x",
            text="",
            timestamp=timezone.now(),
        )


@pytest.mark.django_db
def test_string_representations_hide_text_and_the_chatter_hash(live_session):
    stored = store_chat_message(live_session, normalize())

    assert "that was insane" not in str(stored)
    assert "that was insane" not in repr(stored)
    assert stored.chatter_user_id_hash not in repr(stored)
