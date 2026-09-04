"""Typed errors for stream observation."""


class MonitoringError(Exception):
    """Base class for monitoring-domain failures."""

    code = "monitoring_error"
    message = "The stream could not be observed."


class StreamStateUnavailableError(MonitoringError):
    """The stream state could not be determined.

    This is emphatically **not** an offline observation. It means Twitch timed
    out, failed, could not be authenticated, or returned something unreadable,
    and ClipperStash therefore knows nothing new about the broadcast. No session
    state is changed when this is raised.
    """

    code = "stream_state_unavailable"
    message = "The stream status could not be checked right now."


class ChatConfigurationError(MonitoringError):
    """Chat ingestion is not configured, so nothing may be persisted."""

    code = "chat_not_configured"
    message = "Chat ingestion is not configured for this installation."


class ChatMessageRejected(MonitoringError):
    """A notification was not a usable chat message for the expected channel.

    Rejected messages are dropped, never guessed at and never filed against a
    session they may not belong to.
    """

    code = "chat_message_rejected"
    message = "The chat notification could not be used."


class StreamNotLiveError(MonitoringError):
    """Chat was requested for a streamer with no live session.

    Chat never creates a session: stream lifecycle stays the sole responsibility
    of stream observation.
    """

    code = "stream_not_live"
    message = "That streamer has no live stream session."


class ChatAuthorizationError(MonitoringError):
    """The Twitch connection cannot read chat.

    Either no account is connected, or the connection predates the
    `user:read:chat` scope and must be re-authorized.
    """

    code = "chat_not_authorized"
    message = "Reconnect the Twitch account to grant chat access."
