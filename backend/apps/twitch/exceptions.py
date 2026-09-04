"""Typed errors for the Twitch integration.

Callers outside this package should only ever see these exceptions, never the
underlying HTTP library's exceptions. Messages are built from status codes and
Twitch's own error text; request bodies, headers and credentials are never
included, so an exception can be logged or surfaced without leaking a secret.
"""


class TwitchError(Exception):
    """Base class for every Twitch integration failure."""


class TwitchConfigurationError(TwitchError):
    """The Twitch application is not configured, or is configured incompletely."""


class TwitchAuthenticationError(TwitchError):
    """A token was rejected, expired or revoked, or an OAuth grant failed.

    The connection generally needs to be re-authorized by the operator.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TwitchAPIError(TwitchError):
    """Twitch returned an unexpected response, or could not be reached."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TwitchTransportError(TwitchAPIError):
    """The request could not be completed over the network.

    A subclass of `TwitchAPIError`, so every existing handler keeps working. It
    exists because this failure is *ambiguous* in a way an HTTP status is not: a
    timeout or dropped connection can happen before Twitch saw the request,
    while it was acting on it, or after it answered. Callers whose request has a
    side effect need to tell that apart from a definite refusal.
    """


class TwitchOAuthStateError(TwitchError):
    """The OAuth `state` was missing, unknown, expired, malformed or reused."""


class TwitchAuthorizationDeniedError(TwitchError):
    """The operator declined the authorization request on Twitch."""
