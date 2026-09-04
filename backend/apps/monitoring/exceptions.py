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
