"""Tests for the EventSub WebSocket lifecycle.

No socket is opened: `ScriptedSocketFactory` replays frames in process.
"""

from __future__ import annotations

import pytest

from apps.twitch.client import EVENTSUB_WEBSOCKET_URL
from apps.twitch.eventsub.client import EventSubConnectionError
from apps.twitch.eventsub.messages import EventSubProtocolError, parse_message, session_info
from apps.twitch.eventsub.runtime import EventSubRevoked, EventSubRuntime

from .chat_fixtures import (
    RECONNECT_URL,
    SESSION_ID,
    FakeSocket,
    ScriptedSocketFactory,
    keepalive_frame,
    notification_frame,
    reconnect_frame,
    revocation_frame,
    welcome_frame,
)


def build_runtime(factory, **kwargs):
    subscriptions: list[str] = []
    notifications: list = []
    runtime = EventSubRuntime(
        subscribe=subscriptions.append,
        handle_notification=notifications.append,
        socket_factory=factory,
        **kwargs,
    )
    return runtime, subscriptions, notifications


# -- welcome and subscription -------------------------------------------------


def test_the_base_url_is_the_official_twitch_endpoint():
    assert EVENTSUB_WEBSOCKET_URL == "wss://eventsub.wss.twitch.tv/ws"


def test_welcome_is_parsed_and_the_subscription_uses_its_session_id():
    factory = ScriptedSocketFactory([[welcome_frame(), keepalive_frame()]])
    runtime, subscriptions, _ = build_runtime(factory, max_connections=1)

    runtime.run()

    assert factory.urls == [EVENTSUB_WEBSOCKET_URL]
    assert subscriptions == [SESSION_ID]
    assert runtime.stats.connections == 1


def test_the_subscription_is_created_before_any_other_frame_is_read():
    """Twitch expects the subscription promptly after welcome."""
    order: list[str] = []
    factory = ScriptedSocketFactory([[welcome_frame(), notification_frame()]])
    runtime = EventSubRuntime(
        subscribe=lambda session_id: order.append("subscribe"),
        handle_notification=lambda message: order.append("notification"),
        socket_factory=factory,
        max_connections=1,
    )

    runtime.run()

    assert order == ["subscribe", "notification"]


def test_a_missing_welcome_is_treated_as_a_failed_connection():
    factory = ScriptedSocketFactory([[keepalive_frame()]])
    runtime, subscriptions, _ = build_runtime(factory, max_connections=1)

    with pytest.raises(EventSubProtocolError):
        runtime.run()

    assert subscriptions == []


def test_the_negotiated_keepalive_timeout_is_used():
    factory = ScriptedSocketFactory([[welcome_frame(keepalive_timeout_seconds=30)]])
    runtime, _, _ = build_runtime(factory, max_connections=1)

    runtime.run()

    # The welcome read uses the default; the pump read uses Twitch's value plus grace.
    assert factory.sockets[0].recv_timeouts[-1] == pytest.approx(35.0)


def test_an_absent_keepalive_value_falls_back_to_a_default():
    factory = ScriptedSocketFactory([[welcome_frame(keepalive_timeout_seconds=None)]])
    runtime, _, _ = build_runtime(factory, max_connections=1)

    runtime.run()

    assert factory.sockets[0].recv_timeouts[-1] == pytest.approx(15.0)


# -- message handling ---------------------------------------------------------


def test_keepalives_are_accepted_and_counted():
    factory = ScriptedSocketFactory([[welcome_frame(), keepalive_frame(), keepalive_frame()]])
    runtime, _, notifications = build_runtime(factory, max_connections=1)

    runtime.run()

    assert runtime.stats.keepalives == 2
    assert notifications == []


def test_notifications_are_dispatched():
    factory = ScriptedSocketFactory([[welcome_frame(), notification_frame()]])
    runtime, _, notifications = build_runtime(factory, max_connections=1)

    runtime.run()

    assert len(notifications) == 1
    assert notifications[0].message_type == "notification"
    assert runtime.stats.notifications == 1


@pytest.mark.parametrize(
    "frame",
    [
        "not json",
        "[]",
        '{"no": "metadata"}',
        '{"metadata": {}}',
        '{"metadata": {"message_type": 4}}',
    ],
)
def test_unreadable_frames_are_dropped_without_stopping_the_run(frame):
    factory = ScriptedSocketFactory([[welcome_frame(), frame, notification_frame()]])
    runtime, _, notifications = build_runtime(factory, max_connections=1)

    runtime.run()

    assert runtime.stats.protocol_errors == 1
    assert len(notifications) == 1, "a bad frame must not stop later good ones"


def test_an_unknown_message_type_is_ignored():
    unknown = '{"metadata": {"message_id": "x", "message_type": "future_thing"}, "payload": {}}'
    factory = ScriptedSocketFactory([[welcome_frame(), unknown, notification_frame()]])
    runtime, _, notifications = build_runtime(factory, max_connections=1)

    runtime.run()

    assert runtime.stats.unknown_messages == 1
    assert len(notifications) == 1


def test_nothing_is_ever_sent_to_the_socket():
    """EventSub WebSockets are receive-oriented; only protocol pongs leave."""
    factory = ScriptedSocketFactory([[welcome_frame(), keepalive_frame(), notification_frame()]])
    runtime, _, _ = build_runtime(factory, max_connections=1)

    runtime.run()

    assert all(socket.sent == [] for socket in factory.sockets)


# -- reconnect handoff --------------------------------------------------------


def test_a_reconnect_uses_twitch_url_verbatim_and_does_not_resubscribe():
    factory = ScriptedSocketFactory(
        [
            [welcome_frame(), reconnect_frame()],
            [welcome_frame(session_id="replacement-session"), notification_frame()],
        ]
    )
    runtime, subscriptions, notifications = build_runtime(factory, max_connections=2)

    runtime.run()

    assert factory.urls == [EVENTSUB_WEBSOCKET_URL, RECONNECT_URL]
    assert subscriptions == [SESSION_ID], "Twitch carries subscriptions across a handoff"
    assert runtime.stats.handoffs == 1
    assert runtime.stats.subscriptions_created == 1
    assert len(notifications) == 1


def test_the_old_socket_is_retired_only_after_the_replacement_welcomes():
    closed_when_replacement_opened: list[bool] = []

    class WatchingFactory(ScriptedSocketFactory):
        def __call__(self, url):
            if self.sockets:
                closed_when_replacement_opened.append(self.sockets[0].closed)
            return super().__call__(url)

    factory = WatchingFactory(
        [
            [welcome_frame(), reconnect_frame()],
            [welcome_frame(session_id="replacement-session")],
        ]
    )
    runtime, _, _ = build_runtime(factory, max_connections=2)

    runtime.run()

    assert closed_when_replacement_opened == [False], "the old socket outlives the handover"
    assert factory.sockets[0].closed is True, "and is retired afterwards"


def test_a_reconnect_without_a_url_falls_back_to_a_fresh_connection():
    factory = ScriptedSocketFactory(
        [
            [welcome_frame(), reconnect_frame(reconnect_url=None)],
            [welcome_frame(session_id="fresh-session")],
        ]
    )
    runtime, subscriptions, _ = build_runtime(factory, max_connections=2)

    runtime.run()

    assert factory.urls == [EVENTSUB_WEBSOCKET_URL, EVENTSUB_WEBSOCKET_URL]
    assert subscriptions == [SESSION_ID, "fresh-session"], "a fresh session needs a subscription"


def test_a_failed_handoff_falls_back_to_a_fresh_connection():
    factory = ScriptedSocketFactory(
        [
            [welcome_frame(), reconnect_frame()],
            [EventSubConnectionError("handoff refused")],
            [welcome_frame(session_id="fresh-session")],
        ]
    )
    # Two sockets actually open: the handoff attempt never gets a welcome.
    runtime, subscriptions, _ = build_runtime(factory, max_connections=2)

    runtime.run()

    assert factory.urls == [EVENTSUB_WEBSOCKET_URL, RECONNECT_URL, EVENTSUB_WEBSOCKET_URL]
    assert subscriptions == [SESSION_ID, "fresh-session"]
    assert runtime.stats.connections == 2


# -- ordinary loss ------------------------------------------------------------


def test_silence_past_the_keepalive_window_reconnects_and_resubscribes():
    factory = ScriptedSocketFactory(
        [
            [welcome_frame(), TimeoutError],
            [welcome_frame(session_id="second-session")],
        ]
    )
    runtime, subscriptions, _ = build_runtime(factory, max_connections=2)

    runtime.run()

    assert factory.urls == [EVENTSUB_WEBSOCKET_URL, EVENTSUB_WEBSOCKET_URL]
    assert subscriptions == [SESSION_ID, "second-session"]
    assert runtime.stats.reconnects == 1


def test_a_dead_socket_reconnects_and_resubscribes():
    factory = ScriptedSocketFactory(
        [
            [welcome_frame(), EventSubConnectionError("socket died")],
            [welcome_frame(session_id="second-session")],
        ]
    )
    runtime, subscriptions, _ = build_runtime(factory, max_connections=2)

    runtime.run()

    assert subscriptions == [SESSION_ID, "second-session"]
    assert factory.sockets[0].closed is True


def test_reconnection_stops_at_the_connection_budget():
    factory = ScriptedSocketFactory([[welcome_frame(), TimeoutError]] * 5)
    runtime, subscriptions, _ = build_runtime(factory, max_connections=2)

    runtime.run()

    assert runtime.stats.connections == 2
    assert len(subscriptions) == 2


def test_should_continue_stops_the_run_cleanly():
    calls = {"count": 0}

    def should_continue() -> bool:
        calls["count"] += 1
        return calls["count"] <= 2

    factory = ScriptedSocketFactory([[welcome_frame(), keepalive_frame(), keepalive_frame()]])
    runtime, _, _ = build_runtime(factory, should_continue=should_continue)

    runtime.run()

    assert factory.sockets[0].closed is True


# -- revocation ---------------------------------------------------------------


def test_revocation_stops_the_runtime_with_a_reason():
    factory = ScriptedSocketFactory([[welcome_frame(), revocation_frame("user_removed")]])
    runtime, _, _ = build_runtime(factory, max_connections=1)

    with pytest.raises(EventSubRevoked) as exc_info:
        runtime.run()

    assert exc_info.value.reason == "user_removed"
    assert factory.sockets[0].closed is True


def test_revocation_without_a_status_still_reports_a_reason():
    factory = ScriptedSocketFactory(
        [[welcome_frame(), '{"metadata": {"message_id": "r", "message_type": "revocation"}}']]
    )
    runtime, _, _ = build_runtime(factory, max_connections=1)

    with pytest.raises(EventSubRevoked) as exc_info:
        runtime.run()

    assert exc_info.value.reason == "unspecified"


# -- envelope parsing ---------------------------------------------------------


def test_session_info_reads_the_welcome_session():
    info = session_info(parse_message(welcome_frame()))

    assert info.session_id == SESSION_ID
    assert info.keepalive_timeout_seconds == 10
    assert info.reconnect_url is None


def test_session_info_reads_the_reconnect_url():
    info = session_info(parse_message(reconnect_frame()))

    assert info.reconnect_url == RECONNECT_URL


def test_session_info_rejects_a_session_without_an_id():
    frame = (
        '{"metadata": {"message_id": "w", "message_type": "session_welcome"}, '
        '"payload": {"session": {}}}'
    )

    with pytest.raises(EventSubProtocolError):
        session_info(parse_message(frame))


def test_parse_message_accepts_bytes():
    message = parse_message(welcome_frame().encode("utf-8"))

    assert message.message_type == "session_welcome"


def test_parse_message_rejects_undecodable_bytes():
    with pytest.raises(EventSubProtocolError):
        parse_message(b"\xff\xfe")


def test_envelope_timestamp_is_timezone_aware():
    message = parse_message(notification_frame())

    assert message.message_timestamp is not None
    assert message.message_timestamp.tzinfo is not None


def test_continuation_is_checked_before_replacing_a_socket():
    """A caller that stops mid-connection must not get another socket opened."""
    keep_going = {"value": True}

    def should_continue() -> bool:
        return keep_going["value"]

    class StoppingSocket(FakeSocket):
        def recv(self, timeout=None):
            frame = super().recv(timeout=timeout)
            keep_going["value"] = False
            return frame

    opened: list[str] = []

    def factory(url: str):
        opened.append(url)
        return StoppingSocket([welcome_frame(), reconnect_frame()])

    runtime = EventSubRuntime(
        subscribe=lambda session_id: None,
        handle_notification=lambda message: None,
        socket_factory=factory,
        should_continue=should_continue,
    )

    runtime.run()

    assert opened == [EVENTSUB_WEBSOCKET_URL], "no replacement socket is opened"
    assert runtime.stats.handoffs == 0
    assert runtime.stats.reconnects == 0
