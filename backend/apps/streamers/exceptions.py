"""Typed errors for streamer resolution.

Each carries a short, stable machine code alongside a message that is safe to
show a user. Nothing here ever carries Twitch response bodies, credentials or
internal detail.
"""


class StreamerError(Exception):
    """Base class for streamer-domain failures."""

    code = "streamer_error"
    message = "The streamer could not be resolved."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message


class StreamerInputError(StreamerError):
    """The submitted value is not a Twitch channel URL or login."""

    code = "invalid_streamer_input"
    message = "Enter a valid Twitch channel URL or username."


class StreamerNotFoundError(StreamerError):
    """The login parsed cleanly, but Twitch has no such account."""

    code = "streamer_not_found"
    message = "That Twitch streamer could not be found."


class StreamerConflictError(StreamerError):
    """The Twitch login is already recorded against a different Twitch account.

    Twitch releases abandoned logins for re-registration, so a stored streamer
    can hold a username that now belongs to someone else. V0 does not track
    username history, so rather than silently rewriting one record's identity
    with another's, the conflict is surfaced.
    """

    code = "streamer_conflict"
    message = "That Twitch username is already recorded for a different account."
