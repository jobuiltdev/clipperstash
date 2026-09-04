"""Persistence for the locally connected Twitch identity.

This is not ClipperStash end-user authentication. It records the single Twitch
account the local operator has authorized, so that later milestones can act on
that account's behalf.

Token lifecycle: `token_expires_at` is retained as metadata describing what
Twitch reported when the grant was issued. It is not used to decide when to
refresh — Twitch's guidance is to react to a 401 Unauthorized instead, which the
service layer does with a single bounded refresh-and-retry.

Secret-at-rest: the OAuth tokens are stored as plain columns. That is adequate
for local V0 development and is **not** a production posture — protecting these
values at rest (disk/volume encryption, a managed secret store, or column
encryption with real key management) is a deployment and security concern that
has not been designed yet. All token reads and writes go through the accessors
below rather than through direct attribute access, so that protection can be
introduced in one place without touching callers.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.twitch.oauth import CHAT_READ_SCOPE, REQUIRED_SCOPES

# Window used by `is_token_expired`, which is informational only. Nothing on the
# automatic request path consults it: Twitch's guidance is to react to a 401
# rather than to refresh from a locally tracked expiry.
EXPIRY_SAFETY_MARGIN = timedelta(seconds=60)


class TwitchConnectionQuerySet(models.QuerySet["TwitchConnection"]):
    def current(self) -> TwitchConnection | None:
        """The active connection.

        V0 maintains a single local connection. The lookup is written as an
        ordered query rather than a hardcoded primary key so that scoping it to
        an owner later is a change of filter, not a change of shape.
        """
        return self.order_by("-updated_at").first()


class TwitchConnection(models.Model):
    """A Twitch account that has authorized this ClipperStash installation."""

    twitch_user_id = models.CharField(max_length=64, unique=True)
    login = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255)

    access_token = models.TextField(editable=False)
    refresh_token = models.TextField(editable=False)
    # Metadata: what Twitch reported at grant time. It is kept for diagnostics
    # and is deliberately not the trigger for an automatic refresh.
    token_expires_at = models.DateTimeField()
    scopes = models.JSONField(default=list)

    requires_reauthorization = models.BooleanField(default=False)
    connected_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TwitchConnectionQuerySet.as_manager()

    class Meta:
        verbose_name = "Twitch connection"
        verbose_name_plural = "Twitch connections"
        ordering = ("-updated_at",)

    def __str__(self) -> str:
        return f"Twitch connection: {self.login or self.twitch_user_id}"

    def __repr__(self) -> str:
        # Never include token material in debugging output.
        return f"<TwitchConnection twitch_user_id={self.twitch_user_id!r} login={self.login!r}>"

    # -- token accessors -----------------------------------------------------
    #
    # The single choke point for reading and writing token material.

    def get_access_token(self) -> str:
        return self.access_token

    def get_refresh_token(self) -> str:
        return self.refresh_token

    def apply_tokens(
        self,
        *,
        access_token: str,
        expires_in: int,
        refresh_token: str | None = None,
        scopes: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        """Record a new grant in memory. The caller is responsible for saving.

        A refresh token is only overwritten when Twitch returns one, because a
        refresh response may legitimately omit it and the existing token then
        remains valid.
        """
        self.access_token = access_token
        self.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
        if refresh_token:
            self.refresh_token = refresh_token
        if scopes is not None:
            self.scopes = list(scopes)
        self.requires_reauthorization = False

    def is_token_expired(self, *, margin: timedelta = EXPIRY_SAFETY_MARGIN) -> bool:
        """Whether the locally tracked expiry has passed. Informational only.

        This is a hint for diagnostics, not a refresh trigger. A token can be
        revoked long before this returns True, and can still be accepted after
        it returns True, so only Twitch's own 401 drives a refresh.
        """
        return timezone.now() >= self.token_expires_at - margin

    def mark_requires_reauthorization(self) -> None:
        self.requires_reauthorization = True
        self.save(update_fields=["requires_reauthorization", "updated_at"])

    # -- capabilities --------------------------------------------------------
    #
    # A connection authorized before a scope was added is still a valid
    # connection: its tokens work for what it was granted. It simply cannot do
    # the newer thing until the operator re-authorizes, so capability is
    # reported rather than the row being discarded.

    def granted_scopes(self) -> set[str]:
        return {str(scope) for scope in (self.scopes or [])}

    def missing_scopes(self) -> tuple[str, ...]:
        granted = self.granted_scopes()
        return tuple(scope for scope in REQUIRED_SCOPES if scope not in granted)

    @property
    def can_read_chat(self) -> bool:
        """Whether this connection may open an EventSub chat subscription."""
        return CHAT_READ_SCOPE in self.granted_scopes() and not self.requires_reauthorization

    @property
    def needs_reauthorization(self) -> bool:
        """True when Twitch rejected the connection, or a required scope is absent."""
        return self.requires_reauthorization or bool(self.missing_scopes())
