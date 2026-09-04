"""Tests for the chat monitor and its management command.

Every Twitch interaction is mocked: HTTP through `httpx.MockTransport`, the
WebSocket through scripted in-process frames.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.monitoring.chat_monitor import (
    ChatMonitor,
    require_chat_connection,
    resolve_target_session,
)
from apps.monitoring.exceptions import (
    ChatAuthorizationError,
    ChatConfigurationError,
    StreamNotLiveError,
)
from apps.monitoring.models import ChatMessage, StreamSession, StreamSessionStatus
from apps.twitch.client import TwitchClient
from apps.twitch.exceptions import TwitchAuthenticationError
from apps.twitch.tests.conftest import ALL_FAKE_SECRETS, json_response, routed_transport
from apps.twitch.tests.conftest import token_payload as twitch_token_payload
from conftest import FAKE_CLIENT_SECRET

from .chat_fixtures import (
    HELIX_EVENTSUB_URL,
    ScriptedSocketFactory,
    chat_event,
    keepalive_frame,
    notification_frame,
    revocation_frame,
    subscription_transport,
    welcome_frame,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def hash_secret(settings):
    settings.CHAT_USER_HASH_SECRET = "unit-test-hash-secret"


@pytest.fixture
def live_session(db, streamer) -> StreamSession:
    return StreamSession.objects.create(
        streamer=streamer,
        platform_stream_id="stream-1",
        started_at=timezone.now() - timedelta(hours=1),
        last_observed_at=timezone.now(),
        status=StreamSessionStatus.LIVE,
    )


def run_monitor(session, connection, frames, *, http=None, max_connections=1):
    monitor = ChatMonitor(
        session,
        connection,
        client=TwitchClient(transport=http or subscription_transport()),
        socket_factory=ScriptedSocketFactory([frames]),
        max_connections=max_connections,
    )
    return monitor.run()


# -- preconditions ------------------------------------------------------------


def test_a_live_session_is_found_for_a_streamer(streamer, live_session):
    assert resolve_target_session(streamer.pk, None) == live_session


def test_a_streamer_without_a_live_session_is_refused(streamer):
    with pytest.raises(StreamNotLiveError):
        resolve_target_session(streamer.pk, None)


def test_an_ended_session_is_refused(streamer, live_session):
    live_session.status = StreamSessionStatus.ENDED
    live_session.save(update_fields=["status"])

    with pytest.raises(StreamNotLiveError):
        resolve_target_session(None, live_session.pk)


def test_an_unknown_session_is_refused(db):
    with pytest.raises(StreamNotLiveError):
        resolve_target_session(None, 9999)


def test_chat_never_creates_a_session(streamer):
    with pytest.raises(StreamNotLiveError):
        resolve_target_session(streamer.pk, None)

    assert StreamSession.objects.count() == 0


def test_a_missing_connection_is_refused(db):
    with pytest.raises(ChatAuthorizationError, match="No Twitch account"):
        require_chat_connection()


def test_a_connection_without_the_chat_scope_is_refused(connection):
    connection.scopes = ["clips:edit"]
    connection.save(update_fields=["scopes"])

    with pytest.raises(ChatAuthorizationError):
        require_chat_connection()


def test_a_rejected_connection_is_refused(connection):
    connection.mark_requires_reauthorization()

    with pytest.raises(ChatAuthorizationError):
        require_chat_connection()


def test_a_capable_connection_is_accepted(connection):
    assert require_chat_connection() == connection


def test_a_missing_hash_secret_fails_before_any_socket_opens(live_session, connection, settings):
    settings.CHAT_USER_HASH_SECRET = ""
    factory = ScriptedSocketFactory([[welcome_frame()]])
    monitor = ChatMonitor(live_session, connection, socket_factory=factory)

    with pytest.raises(ChatConfigurationError):
        monitor.run()

    assert factory.urls == [], "no connection is attempted"


# -- ingestion ----------------------------------------------------------------


def test_messages_are_ingested_end_to_end(live_session, connection):
    stats = run_monitor(
        live_session, connection, [welcome_frame(), keepalive_frame(), notification_frame()]
    )

    assert stats.stored == 1
    assert stats.duplicates == 0
    assert stats.rejected == 0
    assert ChatMessage.objects.count() == 1
    assert live_session.chat_messages.get().text == "that was insane"


def test_the_subscription_targets_the_sessions_broadcaster(live_session, connection):
    http = subscription_transport()
    ChatMonitor(
        live_session,
        connection,
        client=TwitchClient(transport=http),
        socket_factory=ScriptedSocketFactory([[welcome_frame()]]),
        max_connections=1,
    ).run()

    import json as json_module

    body = json_module.loads(http.request_for(HELIX_EVENTSUB_URL).content.decode())
    assert body["condition"]["broadcaster_user_id"] == live_session.streamer.platform_user_id


def test_duplicate_deliveries_are_counted_but_not_stored_twice(live_session, connection):
    stats = run_monitor(
        live_session,
        connection,
        [welcome_frame(), notification_frame(), notification_frame()],
    )

    assert stats.stored == 1
    assert stats.duplicates == 1
    assert ChatMessage.objects.count() == 1


def test_a_message_for_another_broadcaster_is_rejected_not_stored(live_session, connection):
    stats = run_monitor(
        live_session,
        connection,
        [welcome_frame(), notification_frame(event=chat_event(broadcaster_user_id="999"))],
    )

    assert stats.rejected == 1
    assert stats.stored == 0
    assert ChatMessage.objects.count() == 0


def test_an_unreadable_frame_does_not_reach_persistence(live_session, connection):
    stats = run_monitor(
        live_session, connection, [welcome_frame(), "not json", notification_frame()]
    )

    assert stats.stored == 1
    assert stats.runtime.protocol_errors == 1
    assert ChatMessage.objects.count() == 1


def test_an_already_ended_session_stops_before_reading_any_frame(live_session, connection):
    """The run does not sit connected discarding messages; it stops."""
    live_session.status = StreamSessionStatus.ENDED
    live_session.save(update_fields=["status"])

    stats = run_monitor(live_session, connection, [welcome_frame(), notification_frame()])

    assert stats.stored == 0
    assert stats.rejected == 0, "the notification is never even read"
    assert stats.stopped_because_session_ended is True
    assert stats.runtime.notifications == 0
    assert ChatMessage.objects.count() == 0


# -- failure semantics --------------------------------------------------------


def test_revocation_stops_ingestion_without_touching_the_session(live_session, connection):
    stats = run_monitor(
        live_session,
        connection,
        [welcome_frame(), notification_frame(), revocation_frame("authorization_revoked")],
    )

    assert stats.revoked_reason == "authorization_revoked"

    live_session.refresh_from_db()
    assert live_session.status == StreamSessionStatus.LIVE, "chat failure is not stream offline"
    assert live_session.ended_at is None
    assert ChatMessage.objects.count() == 1, "history is preserved"


def test_revocation_marks_the_connection_for_reauthorization(live_session, connection):
    run_monitor(live_session, connection, [welcome_frame(), revocation_frame()])

    connection.refresh_from_db()
    assert connection.requires_reauthorization is True
    assert connection.can_read_chat is False


def test_a_subscription_auth_failure_does_not_close_the_session(live_session, connection):
    http = routed_transport(
        {
            HELIX_EVENTSUB_URL: [
                json_response(401, {"message": "Invalid OAuth token"}),
                json_response(401, {"message": "Invalid OAuth token"}),
            ],
            "https://id.twitch.tv/oauth2/token": json_response(200, twitch_token_payload()),
        }
    )

    with pytest.raises(TwitchAuthenticationError):
        run_monitor(live_session, connection, [welcome_frame()], http=http)

    live_session.refresh_from_db()
    assert live_session.status == StreamSessionStatus.LIVE
    assert live_session.ended_at is None


def test_a_websocket_failure_does_not_close_the_session(live_session, connection):
    run_monitor(live_session, connection, [welcome_frame(), TimeoutError], max_connections=1)

    live_session.refresh_from_db()
    assert live_session.status == StreamSessionStatus.LIVE
    assert live_session.ended_at is None


def test_no_secret_is_logged_during_a_run(live_session, connection, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        run_monitor(
            live_session, connection, [welcome_frame(), keepalive_frame(), notification_frame()]
        )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (*ALL_FAKE_SECRETS, FAKE_CLIENT_SECRET, "unit-test-hash-secret"):
        assert secret not in logged
    assert "that was insane" not in logged, "chat text is not logged by default"
    assert "900001" not in logged, "the raw chatter id is not logged"


# -- management command -------------------------------------------------------


def call_monitor_chat(*args, **kwargs) -> str:
    out = StringIO()
    call_command("monitor_chat", *args, stdout=out, stderr=StringIO(), **kwargs)
    return out.getvalue()


def test_the_command_rejects_an_unknown_streamer(db):
    with pytest.raises(CommandError, match="No resolved streamer"):
        call_monitor_chat(9999)


def test_the_command_rejects_an_offline_streamer(streamer, connection):
    with pytest.raises(CommandError, match="stream_not_live"):
        call_monitor_chat(streamer.pk)


def test_the_command_rejects_an_ended_session(live_session, connection):
    live_session.status = StreamSessionStatus.ENDED
    live_session.save(update_fields=["status"])

    with pytest.raises(CommandError, match="stream_not_live"):
        call_monitor_chat(session_id=live_session.pk)


def test_the_command_rejects_a_missing_connection(streamer, live_session):
    with pytest.raises(CommandError, match="chat_not_authorized"):
        call_monitor_chat(streamer.pk)


def test_the_command_rejects_a_connection_without_the_chat_scope(
    streamer, live_session, connection
):
    connection.scopes = ["clips:edit"]
    connection.save(update_fields=["scopes"])

    with pytest.raises(CommandError, match="chat_not_authorized"):
        call_monitor_chat(streamer.pk)


def test_the_command_runs_the_monitor_for_a_valid_setup(
    streamer, live_session, connection, monkeypatch
):
    from apps.monitoring import chat_monitor as module

    monkeypatch.setattr(
        module.ChatMonitor,
        "_subscribe",
        lambda self, session_id: None,
    )
    monkeypatch.setattr(
        module,
        "open_socket",
        lambda url: ScriptedSocketFactory([[welcome_frame(), notification_frame()]])(url),
    )

    output = call_monitor_chat(streamer.pk, max_connections=1)

    assert "Monitoring chat" in output
    assert "Stored 1 message(s)" in output
    assert ChatMessage.objects.count() == 1


def test_the_command_reports_a_revocation(streamer, live_session, connection, monkeypatch):
    from apps.monitoring import chat_monitor as module

    monkeypatch.setattr(module.ChatMonitor, "_subscribe", lambda self, session_id: None)
    monkeypatch.setattr(
        module,
        "open_socket",
        lambda url: ScriptedSocketFactory([[welcome_frame(), revocation_frame()]])(url),
    )

    output = call_monitor_chat(streamer.pk, max_connections=1)

    assert "revoked" in output.lower()


def test_the_command_requires_a_target(db):
    with pytest.raises(CommandError):
        call_command("monitor_chat", stdout=StringIO(), stderr=StringIO())
