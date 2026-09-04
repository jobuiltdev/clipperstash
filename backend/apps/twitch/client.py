"""Centralized Twitch HTTP client.

Every outbound Twitch request in ClipperStash goes through this module. Domain
code must not build Twitch requests of its own: it calls the services layer,
which calls this client.

Secret handling: tokens and the client secret are only ever placed in request
headers or form bodies. They are never put into URLs, log records or exception
messages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from django.conf import settings
from django.utils.dateparse import parse_datetime

from apps.twitch.exceptions import (
    TwitchAPIError,
    TwitchAuthenticationError,
    TwitchConfigurationError,
)

logger = logging.getLogger(__name__)

HELIX_BASE_URL = "https://api.twitch.tv/helix"
OAUTH_AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
OAUTH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
OAUTH_VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"

DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class TwitchCredentials:
    """Twitch application credentials, resolved from Django settings."""

    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str

    def __post_init__(self) -> None:
        missing = [
            name
            for name, value in (
                ("TWITCH_CLIENT_ID", self.client_id),
                ("TWITCH_CLIENT_SECRET", self.client_secret),
                ("TWITCH_REDIRECT_URI", self.redirect_uri),
            )
            if not value
        ]
        if missing:
            raise TwitchConfigurationError(
                "Twitch application is not configured. Missing settings: " + ", ".join(missing)
            )

    @classmethod
    def from_settings(cls) -> TwitchCredentials:
        return cls(
            client_id=getattr(settings, "TWITCH_CLIENT_ID", ""),
            client_secret=getattr(settings, "TWITCH_CLIENT_SECRET", ""),
            redirect_uri=getattr(settings, "TWITCH_REDIRECT_URI", ""),
        )


@dataclass(frozen=True)
class TokenResponse:
    """A token grant returned by Twitch's OAuth token endpoint."""

    access_token: str = field(repr=False)
    expires_in: int
    token_type: str = "bearer"
    refresh_token: str | None = field(default=None, repr=False)
    scopes: tuple[str, ...] = ()

    def __str__(self) -> str:
        return f"TokenResponse(expires_in={self.expires_in}, scopes={list(self.scopes)})"


@dataclass(frozen=True)
class TokenValidation:
    """The result of Twitch's token validation endpoint for a user token."""

    user_id: str
    login: str
    client_id: str
    scopes: tuple[str, ...]
    expires_in: int


@dataclass(frozen=True)
class TwitchIdentity:
    """A Twitch account as returned by `GET /helix/users`.

    Only the fields ClipperStash actually uses are modelled; the rest of Twitch's
    user payload is deliberately dropped rather than carried around.
    """

    user_id: str
    login: str
    display_name: str
    profile_image_url: str = ""
    broadcaster_type: str = ""
    description: str = ""


@dataclass(frozen=True)
class TwitchStream:
    """A live broadcast as returned by `GET /helix/streams`.

    `stream_id` is Twitch's identity for one broadcast and is what ClipperStash
    uses to tell one session apart from the next. `started_at` is Twitch's own
    authoritative broadcast start, always timezone-aware.
    """

    stream_id: str
    user_id: str
    user_login: str
    user_name: str
    game_id: str
    game_name: str
    stream_type: str
    title: str
    viewer_count: int
    started_at: datetime
    language: str
    thumbnail_url: str
    is_mature: bool


def stream_from_helix_entry(entry: Any, *, expected_user_id: str | None = None) -> TwitchStream:
    """Build a stream from one `GET /helix/streams` entry.

    Anything that is not a usable live-stream object raises, rather than being
    reported as "not live": a payload we cannot read means the stream state is
    unknown, which is a different thing from the broadcaster being offline.
    """
    if not isinstance(entry, dict):
        raise TwitchAPIError("Twitch returned an unexpected stream object.")

    stream_id = str(entry.get("id") or "")
    user_id = str(entry.get("user_id") or "")
    if not stream_id or not user_id:
        raise TwitchAPIError("Twitch returned a stream without an id or user id.")

    if expected_user_id is not None and user_id != expected_user_id:
        raise TwitchAPIError("Twitch returned a stream belonging to a different broadcaster.")

    # Twitch marks live broadcasts as "live". Any other value is a shape this
    # milestone does not model, so it is treated as unreadable rather than
    # quietly accepted as a live broadcast.
    stream_type = str(entry.get("type") or "")
    if stream_type != "live":
        raise TwitchAPIError("Twitch returned a stream that is not marked live.")

    started_at = parse_started_at(entry.get("started_at"))

    viewer_count = entry.get("viewer_count")
    if not isinstance(viewer_count, int) or isinstance(viewer_count, bool) or viewer_count < 0:
        raise TwitchAPIError("Twitch returned a stream without a usable viewer count.")

    return TwitchStream(
        stream_id=stream_id,
        user_id=user_id,
        user_login=str(entry.get("user_login") or ""),
        user_name=str(entry.get("user_name") or ""),
        game_id=str(entry.get("game_id") or ""),
        game_name=str(entry.get("game_name") or ""),
        stream_type=stream_type,
        title=str(entry.get("title") or ""),
        viewer_count=viewer_count,
        started_at=started_at,
        language=str(entry.get("language") or ""),
        thumbnail_url=str(entry.get("thumbnail_url") or ""),
        is_mature=bool(entry.get("is_mature")),
    )


def parse_started_at(value: Any) -> datetime:
    """Parse Twitch's broadcast start into a timezone-aware datetime.

    A start time we cannot read makes the whole observation unusable. The local
    clock is never substituted: only Twitch knows when the broadcast began.
    """
    if not isinstance(value, str) or not value:
        raise TwitchAPIError("Twitch returned a stream without a start time.")
    try:
        parsed = parse_datetime(value)
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise TwitchAPIError("Twitch returned an unreadable stream start time.")
    return parsed


def identity_from_helix_user(entry: Any) -> TwitchIdentity:
    """Build an identity from one `GET /helix/users` entry.

    A payload that is not a usable user object is an integration error rather
    than a "no such user" result, so it raises instead of returning None.
    """
    if not isinstance(entry, dict):
        raise TwitchAPIError("Twitch returned an unexpected user object.")

    user_id = str(entry.get("id") or "")
    login = str(entry.get("login") or "")
    if not user_id or not login:
        raise TwitchAPIError("Twitch returned a user without an id or login.")

    return TwitchIdentity(
        user_id=user_id,
        login=login,
        display_name=str(entry.get("display_name") or ""),
        profile_image_url=str(entry.get("profile_image_url") or ""),
        broadcaster_type=str(entry.get("broadcaster_type") or ""),
        description=str(entry.get("description") or ""),
    )


def normalize_scopes(payload: Any) -> tuple[str, ...]:
    """Normalize Twitch's scope field, which may be a list or a space-joined string."""
    if isinstance(payload, str):
        return tuple(part for part in payload.split(" ") if part)
    if isinstance(payload, list):
        return tuple(str(part) for part in payload if part)
    return ()


class TwitchClient:
    """Thin, explicit wrapper over the Twitch Helix and Identity APIs."""

    def __init__(
        self,
        credentials: TwitchCredentials | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.credentials = credentials or TwitchCredentials.from_settings()
        self.timeout = timeout
        self._transport = transport

    # -- plumbing ------------------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, transport=self._transport)

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            with self._client() as client:
                return client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            raise TwitchAPIError(f"Could not reach Twitch ({type(exc).__name__}).") from None

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            raise TwitchAPIError(
                f"Twitch returned a non-JSON response (HTTP {response.status_code}).",
                status_code=response.status_code,
            ) from None
        if not isinstance(payload, dict):
            raise TwitchAPIError(
                f"Twitch returned an unexpected JSON shape (HTTP {response.status_code}).",
                status_code=response.status_code,
            )
        return payload

    @staticmethod
    def _describe(payload: dict[str, Any]) -> str:
        """Extract Twitch's own error text. Never includes request data."""
        for key in ("message", "error_description", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return "no additional detail"

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            payload = response.json()
        except ValueError:
            payload = None
        detail = self._describe(payload) if isinstance(payload, dict) else "no additional detail"

        if response.status_code in (401, 403):
            raise TwitchAuthenticationError(
                f"Twitch rejected the credentials (HTTP {response.status_code}): {detail}",
                status_code=response.status_code,
            )
        raise TwitchAPIError(
            f"Twitch request failed (HTTP {response.status_code}): {detail}",
            status_code=response.status_code,
        )

    def _token_request(self, data: dict[str, str], *, action: str) -> TokenResponse:
        response = self._send("POST", OAUTH_TOKEN_URL, data=data)
        self._raise_for_status(response)
        payload = self._decode(response)

        access_token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if not isinstance(access_token, str) or not access_token:
            raise TwitchAPIError(f"Twitch {action} response contained no access token.")
        if not isinstance(expires_in, int):
            raise TwitchAPIError(f"Twitch {action} response contained no usable expiry.")

        refresh_token = payload.get("refresh_token")
        logger.info("Twitch %s succeeded (expires_in=%s).", action, expires_in)
        return TokenResponse(
            access_token=access_token,
            expires_in=expires_in,
            token_type=str(payload.get("token_type") or "bearer"),
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
            scopes=normalize_scopes(payload.get("scope")),
        )

    # -- OAuth ---------------------------------------------------------------

    def fetch_app_access_token(self) -> TokenResponse:
        """Client Credentials grant, used for app-only Helix calls."""
        return self._token_request(
            {
                "client_id": self.credentials.client_id,
                "client_secret": self.credentials.client_secret,
                "grant_type": "client_credentials",
            },
            action="app token request",
        )

    def exchange_authorization_code(self, code: str) -> TokenResponse:
        """Authorization Code grant, exchanged server-side."""
        return self._token_request(
            {
                "client_id": self.credentials.client_id,
                "client_secret": self.credentials.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.credentials.redirect_uri,
            },
            action="authorization code exchange",
        )

    def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        """Exchange a refresh token for a fresh user access token."""
        try:
            return self._token_request(
                {
                    "client_id": self.credentials.client_id,
                    "client_secret": self.credentials.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                action="token refresh",
            )
        except TwitchAPIError as exc:
            # Twitch answers an unusable refresh token with 400, not 401.
            if exc.status_code == 400:
                raise TwitchAuthenticationError(
                    "Twitch refused the refresh token; the connection must be re-authorized.",
                    status_code=400,
                ) from None
            raise

    def validate_token(self, access_token: str) -> TokenValidation:
        """Confirm a user access token is still live, and read its identity."""
        response = self._send(
            "GET",
            OAUTH_VALIDATE_URL,
            headers={"Authorization": f"OAuth {access_token}"},
        )
        if response.status_code == 401:
            raise TwitchAuthenticationError(
                "Twitch reports the access token is invalid or revoked.",
                status_code=401,
            )
        self._raise_for_status(response)
        payload = self._decode(response)
        return TokenValidation(
            user_id=str(payload.get("user_id", "")),
            login=str(payload.get("login", "")),
            client_id=str(payload.get("client_id", "")),
            scopes=normalize_scopes(payload.get("scopes")),
            expires_in=int(payload.get("expires_in") or 0),
        )

    # -- Helix ---------------------------------------------------------------

    def helix_get(
        self,
        path: str,
        *,
        access_token: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Authenticated GET against Helix, returning the decoded payload."""
        endpoint = HELIX_BASE_URL + "/" + path.lstrip("/")
        response = self._send(
            "GET",
            endpoint,
            headers={
                "Client-Id": self.credentials.client_id,
                "Authorization": f"Bearer {access_token}",
            },
            params=params or {},
        )
        self._raise_for_status(response)
        return self._decode(response)

    def get_authenticated_user(self, access_token: str) -> TwitchIdentity:
        """GET /helix/users with no query parameters returns the token's own user."""
        payload = self.helix_get("users", access_token=access_token)
        entries = payload.get("data")
        if not isinstance(entries, list) or not entries:
            raise TwitchAPIError("Twitch returned no user for the supplied access token.")
        return identity_from_helix_user(entries[0])

    def get_stream_by_user_id(self, user_id: str, *, access_token: str) -> TwitchStream | None:
        """Look up the broadcaster's current live stream.

        The user id travels as a query parameter and carries no credential.
        Returns None only when Twitch answers successfully with an empty `data`
        list, which is how it reports that the broadcaster is not live. Every
        other outcome raises, so "could not determine" is never mistaken for
        "offline".
        """
        payload = self.helix_get("streams", access_token=access_token, params={"user_id": user_id})
        entries = payload.get("data")
        if not isinstance(entries, list):
            raise TwitchAPIError("Twitch returned an unexpected streams payload.")
        if not entries:
            return None
        return stream_from_helix_entry(entries[0], expected_user_id=user_id)

    def get_user_by_login(self, login: str, *, access_token: str) -> TwitchIdentity | None:
        """Look up a public Twitch account by login.

        The login travels as a query parameter, which is safe: it has already
        been validated as a bare Twitch login and carries no credential. Returns
        None when Twitch answers successfully with an empty `data` list, which is
        how it reports "no such user".
        """
        payload = self.helix_get("users", access_token=access_token, params={"login": login})
        entries = payload.get("data")
        if not isinstance(entries, list):
            raise TwitchAPIError("Twitch returned an unexpected users payload.")
        if not entries:
            return None
        return identity_from_helix_user(entries[0])
