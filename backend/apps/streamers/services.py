"""Streamer resolution.

The flow is: parse the submitted value into a Twitch login, ask Twitch who that
is, then record the answer. The Twitch call goes through the `twitch` app's
service layer, which owns transport and authentication; nothing here builds an
HTTP request, and the submitted URL itself is never fetched.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction

from apps.streamers.exceptions import StreamerConflictError, StreamerNotFoundError
from apps.streamers.models import Platform, Streamer
from apps.streamers.parsers import canonical_channel_url, parse_twitch_login
from apps.twitch import services as twitch_services
from apps.twitch.client import TwitchClient, TwitchIdentity

logger = logging.getLogger(__name__)


def resolve_streamer(submitted: str | None, *, client: TwitchClient | None = None) -> Streamer:
    """Resolve submitted input to a persisted `Streamer`.

    Raises `StreamerInputError` for anything that is not a Twitch channel URL or
    login, `StreamerNotFoundError` when Twitch has no such account, and the
    integration's typed Twitch errors when Twitch itself is unavailable or the
    application is unconfigured.
    """
    login = parse_twitch_login(submitted)

    identity = twitch_services.lookup_user_by_login(login, client=client)
    if identity is None:
        logger.info("Twitch has no account for the requested login.")
        raise StreamerNotFoundError()

    return upsert_twitch_streamer(identity)


@transaction.atomic
def upsert_twitch_streamer(identity: TwitchIdentity) -> Streamer:
    """Create or refresh the streamer for a resolved Twitch identity.

    Resolution is idempotent. The match is made on the Twitch account id, so a
    channel that has been renamed updates its stored username and canonical URL
    rather than producing a second row.
    """
    streamer = Streamer.objects.filter(
        platform=Platform.TWITCH,
        platform_user_id=identity.user_id,
    ).first()

    created = streamer is None
    if streamer is None:
        streamer = Streamer(
            platform=Platform.TWITCH,
            platform_user_id=identity.user_id,
        )

    streamer.username = identity.login
    streamer.display_name = identity.display_name
    streamer.channel_url = canonical_channel_url(identity.login)
    streamer.profile_image_url = identity.profile_image_url
    streamer.broadcaster_type = identity.broadcaster_type
    streamer.description = identity.description
    streamer.is_active = True

    try:
        with transaction.atomic():
            streamer.save()
    except IntegrityError:
        # The only remaining uniqueness left to violate is the username: this
        # login is recorded against a different Twitch account, which happens
        # when a released login is re-registered by someone else.
        raise StreamerConflictError() from None

    logger.info(
        "%s streamer for twitch user_id=%s.",
        "Created" if created else "Refreshed",
        identity.user_id,
    )
    return streamer
