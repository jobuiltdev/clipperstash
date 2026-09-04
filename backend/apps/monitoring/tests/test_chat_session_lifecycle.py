"""The chat monitor stops when stream observation ends its session.

Chat is subordinate to stream lifecycle: it never ends a session, it only
notices that Milestone 3 already did, and then shuts down cleanly. These tests
drive scripted in-process sockets; nothing touches the network.
"""

from __future__ import annotations

import inspect
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.monitoring import chat_monitor as chat_monitor_module
from apps.monitoring.chat_monitor import ChatMonitor
from apps.monitoring.models import ChatMessage, StreamSession, StreamSessionStatus
from apps.twitch.client import EVENTSUB_WEBSOCKET_URL, TwitchClient
from apps.twitch.eventsub.client import EventSubConnectionError

from .chat_fixtures import (
    RECONNECT_URL,
    FakeSocket,
    ScriptedSocketFactory,
    chat_event,
    keepalive_frame,
    notification_frame,
    reconnect_frame,
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


def end_session(session: StreamSession) -> None:
    """End the session the way stream observation would, in the database only."""
    StreamSession.objects.filter(pk=session.pk).update(
        status=StreamSessionStatus.ENDED, ended_at=timezone.now()
    )


class EndingSocket(FakeSocket):
    """Ends the stream session after delivering a chosen frame.

    Models stream observation closing the broadcast in another process while
    this run is mid-receive.
    """

    def __init__(self, frames, *, session, end_after_index: int) -> None:
        super().__init__(frames)
        self._session = session
        self._end_after_index = end_after_index
        self._delivered = 0

    def recv(self, timeout=None):
        frame = super().recv(timeout=timeout)
        self._delivered += 1
        if self._delivered == self._end_after_index:
            end_session(self._session)
        return frame


def monitor_for(session, connection, factory, *, max_connections=None) -> ChatMonitor:
    return ChatMonitor(
        session,
        connection,
        client=TwitchClient(transport=subscription_transport()),
        socket_factory=factory,
        max_connections=max_connections,
    )


# -- 1. a live session ingests normally ---------------------------------------


def test_a_live_session_ingests_normally(live_session, connection):
    factory = ScriptedSocketFactory([[welcome_frame(), keepalive_frame(), notification_frame()]])

    stats = monitor_for(live_session, connection, factory, max_connections=1).run()

    assert stats.stored == 1
    assert stats.stopped_because_session_ended is False
    assert ChatMessage.objects.count() == 1


# -- 2. ending mid-loop stops the run -----------------------------------------


def test_ending_the_session_mid_loop_stops_the_run_cleanly(live_session, connection):
    socket = EndingSocket(
        [welcome_frame(), notification_frame(), notification_frame(envelope_message_id="notify-2")],
        session=live_session,
        end_after_index=2,
    )
    factory = ScriptedSocketFactory([[]])
    factory.sockets = []

    monitor = ChatMonitor(
        live_session,
        connection,
        client=TwitchClient(transport=subscription_transport()),
        socket_factory=lambda url: socket,
    )

    stats = monitor.run()

    assert stats.stopped_because_session_ended is True
    assert socket.closed is True, "the active socket is closed"
    assert stats.stored == 1, "only the message received while live"
    assert ChatMessage.objects.count() == 1
    assert stats.runtime.connections == 1, "no new connection is opened"


def test_no_further_message_is_stored_after_the_session_ends(live_session, connection):
    socket = EndingSocket(
        [
            welcome_frame(),
            notification_frame(),
            notification_frame(
                envelope_message_id="notify-2", event=chat_event(message_id="chat-2")
            ),
        ],
        session=live_session,
        end_after_index=2,
    )

    ChatMonitor(
        live_session,
        connection,
        client=TwitchClient(transport=subscription_transport()),
        socket_factory=lambda url: socket,
    ).run()

    assert ChatMessage.objects.count() == 1
    assert ChatMessage.objects.get().twitch_message_id == "chat-1"


# -- 3. no reconnect after the session ends -----------------------------------


def test_an_ended_session_prevents_an_ordinary_reconnect(live_session, connection):
    """A lost socket is not replaced once the broadcast is over."""
    socket = EndingSocket(
        [welcome_frame(), keepalive_frame(), EventSubConnectionError("socket died")],
        session=live_session,
        end_after_index=2,
    )
    opened: list[str] = []

    def factory(url: str):
        opened.append(url)
        return socket

    stats = ChatMonitor(
        live_session,
        connection,
        client=TwitchClient(transport=subscription_transport()),
        socket_factory=factory,
    ).run()

    assert opened == [EVENTSUB_WEBSOCKET_URL], "no second connection is opened"
    assert stats.runtime.reconnects == 0
    assert stats.runtime.subscriptions_created == 1, "no subscription is recreated"
    assert stats.stopped_because_session_ended is True
    assert socket.closed is True


def test_an_ended_session_prevents_a_keepalive_timeout_reconnect(live_session, connection):
    socket = EndingSocket(
        [welcome_frame(), keepalive_frame(), TimeoutError],
        session=live_session,
        end_after_index=2,
    )
    opened: list[str] = []

    def factory(url: str):
        opened.append(url)
        return socket

    stats = ChatMonitor(
        live_session,
        connection,
        client=TwitchClient(transport=subscription_transport()),
        socket_factory=factory,
    ).run()

    assert opened == [EVENTSUB_WEBSOCKET_URL]
    assert stats.runtime.subscriptions_created == 1


# -- 4. no handoff after the session ends -------------------------------------


def test_an_ended_session_prevents_a_reconnect_handoff(live_session, connection):
    """Twitch asked for a handoff, but there is nothing left to hand off to."""
    socket = EndingSocket(
        [welcome_frame(), keepalive_frame(), reconnect_frame()],
        session=live_session,
        end_after_index=2,
    )
    opened: list[str] = []

    def factory(url: str):
        opened.append(url)
        return socket

    stats = ChatMonitor(
        live_session,
        connection,
        client=TwitchClient(transport=subscription_transport()),
        socket_factory=factory,
    ).run()

    assert RECONNECT_URL not in opened, "no replacement socket is opened"
    assert opened == [EVENTSUB_WEBSOCKET_URL]
    assert stats.runtime.subscriptions_created == 1, "no subscription is recreated"
    assert socket.closed is True
    assert stats.stopped_because_session_ended is True


# -- 5. a keepalive alone is enough to notice ---------------------------------


def test_a_keepalive_alone_is_enough_to_notice_the_session_ended(live_session, connection):
    """A quiet channel must not keep the run alive."""
    socket = EndingSocket(
        [welcome_frame(), keepalive_frame(), keepalive_frame(message_id="keepalive-2")],
        session=live_session,
        end_after_index=2,
    )

    stats = ChatMonitor(
        live_session,
        connection,
        client=TwitchClient(transport=subscription_transport()),
        socket_factory=lambda url: socket,
    ).run()

    assert stats.stopped_because_session_ended is True
    assert stats.runtime.keepalives == 1, "the second keepalive is never read"
    assert socket.closed is True


# -- 6/7. nothing else is mutated ---------------------------------------------


def test_the_stream_session_is_not_further_mutated_by_chat_shutdown(live_session, connection):
    socket = EndingSocket(
        [welcome_frame(), notification_frame(), keepalive_frame()],
        session=live_session,
        end_after_index=2,
    )

    ChatMonitor(
        live_session,
        connection,
        client=TwitchClient(transport=subscription_transport()),
        socket_factory=lambda url: socket,
    ).run()

    live_session.refresh_from_db()
    ended_at = live_session.ended_at
    assert live_session.status == StreamSessionStatus.ENDED
    assert ended_at is not None
    assert live_session.started_at is not None, "history is preserved"
    assert ChatMessage.objects.count() == 1, "stored chat is not deleted"

    # And nothing has moved after the run finished.
    live_session.refresh_from_db()
    assert live_session.ended_at == ended_at


def test_the_connection_is_not_flagged_when_the_session_merely_ends(live_session, connection):
    socket = EndingSocket(
        [welcome_frame(), keepalive_frame(), keepalive_frame(message_id="k2")],
        session=live_session,
        end_after_index=2,
    )

    ChatMonitor(
        live_session,
        connection,
        client=TwitchClient(transport=subscription_transport()),
        socket_factory=lambda url: socket,
    ).run()

    connection.refresh_from_db()
    assert connection.requires_reauthorization is False
    assert connection.can_read_chat is True


# -- 8. a controlled stop is distinct from every failure ----------------------


def test_a_session_end_is_not_reported_as_a_revocation(live_session, connection):
    socket = EndingSocket(
        [welcome_frame(), keepalive_frame(), keepalive_frame(message_id="k2")],
        session=live_session,
        end_after_index=2,
    )

    stats = ChatMonitor(
        live_session,
        connection,
        client=TwitchClient(transport=subscription_transport()),
        socket_factory=lambda url: socket,
    ).run()

    assert stats.stopped_because_session_ended is True
    assert stats.revoked_reason is None


def test_a_revocation_is_not_reported_as_a_session_end(live_session, connection):
    factory = ScriptedSocketFactory([[welcome_frame(), revocation_frame()]])

    stats = monitor_for(live_session, connection, factory, max_connections=1).run()

    assert stats.revoked_reason == "authorization_revoked"
    assert stats.stopped_because_session_ended is False

    live_session.refresh_from_db()
    assert live_session.status == StreamSessionStatus.LIVE


def test_a_socket_failure_on_a_live_session_still_reconnects(live_session, connection):
    """The session-end check must not have broken ordinary recovery."""
    factory = ScriptedSocketFactory(
        [
            [welcome_frame(), EventSubConnectionError("socket died")],
            [welcome_frame(session_id="second-session")],
        ]
    )

    stats = monitor_for(live_session, connection, factory, max_connections=2).run()

    assert stats.runtime.reconnects == 1
    assert stats.runtime.subscriptions_created == 2
    assert stats.stopped_because_session_ended is False


def test_a_handoff_on_a_live_session_still_happens(live_session, connection):
    factory = ScriptedSocketFactory(
        [
            [welcome_frame(), reconnect_frame()],
            [welcome_frame(session_id="replacement-session")],
        ]
    )

    stats = monitor_for(live_session, connection, factory, max_connections=2).run()

    assert factory.urls == [EVENTSUB_WEBSOCKET_URL, RECONNECT_URL]
    assert stats.runtime.handoffs == 1
    assert stats.runtime.subscriptions_created == 1
    assert stats.stopped_because_session_ended is False


def test_a_keepalive_timeout_on_a_live_session_still_reconnects(live_session, connection):
    factory = ScriptedSocketFactory(
        [[welcome_frame(), TimeoutError], [welcome_frame(session_id="second-session")]]
    )

    stats = monitor_for(live_session, connection, factory, max_connections=2).run()

    assert stats.runtime.reconnects == 1
    assert stats.stopped_because_session_ended is False


# -- status is re-read, not remembered ----------------------------------------


def test_the_status_is_re_read_from_the_database_not_the_loaded_object(live_session, connection):
    """The in-memory object stays stale on purpose; the query is what decides."""
    socket = EndingSocket(
        [welcome_frame(), keepalive_frame(), keepalive_frame(message_id="k2")],
        session=live_session,
        end_after_index=2,
    )
    monitor = ChatMonitor(
        live_session,
        connection,
        client=TwitchClient(transport=subscription_transport()),
        socket_factory=lambda url: socket,
    )

    monitor.run()

    assert monitor.session.status == StreamSessionStatus.LIVE, "the loaded object is untouched"
    assert StreamSession.objects.get(pk=live_session.pk).status == StreamSessionStatus.ENDED


def test_the_liveness_check_reads_no_chat_history(
    live_session, connection, django_assert_num_queries
):
    for index in range(3):
        ChatMessage.objects.create(
            session=live_session,
            twitch_event_message_id=f"e-{index}",
            twitch_message_id=f"m-{index}",
            chatter_user_id_hash="hash",
            text="hello",
            timestamp=timezone.now(),
        )

    monitor = ChatMonitor(live_session, connection)

    with django_assert_num_queries(1):
        assert monitor._session_is_live() is True


# -- the socket factory stays late-bound --------------------------------------


def test_the_socket_factory_default_is_not_bound_at_import_time():
    """Guards the bug that once let a patched factory be silently ignored."""
    default = inspect.signature(ChatMonitor.__init__).parameters["socket_factory"].default

    assert default is None, "a callable default here would defeat monkeypatching"


def test_patching_the_module_level_open_socket_is_honoured(live_session, connection, monkeypatch):
    opened: list[str] = []

    def fake_open(url: str):
        opened.append(url)
        return FakeSocket([welcome_frame(), keepalive_frame()])

    monkeypatch.setattr(chat_monitor_module, "open_socket", fake_open)

    ChatMonitor(
        live_session,
        connection,
        client=TwitchClient(transport=subscription_transport()),
        max_connections=1,
    ).run()

    assert opened == [EVENTSUB_WEBSOCKET_URL], "the patched factory is the one used"
