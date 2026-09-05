"""Synthetic chat for calibration tests.

Every sample here is invented. No real Twitch chat, no real chatter identity and
no captured session appears anywhere in this package, and none ever should — the
whole point of the ignored `evaluation_data/` directory is that real material
stays off disk in the repository.

The builders below describe chat the way a calibration question does: "quiet for
a minute, then thirty people reacting for ten seconds". That keeps each scenario
readable as a statement about stream behaviour rather than as a list of rows.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from apps.moments.detector import ChatSample

# A fixed instant, so every window and tick assertion is exact.
START = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

PLAIN_TEXT = "hello everyone"
REACTION_TEXT = "LMAO no way clip it"


def at(seconds: float) -> datetime:
    """An instant this many seconds after the fixed start."""
    return START + timedelta(seconds=seconds)


def chat(
    *,
    start: float,
    end: float,
    count: int,
    chatters: int,
    text: str = PLAIN_TEXT,
    emotes: int = 0,
    chatter_prefix: str = "viewer",
) -> list[ChatSample]:
    """`count` messages spread evenly across `[start, end)` seconds.

    Timestamps are placed deterministically, so a scenario always produces
    byte-identical input. `chatters` distinct pseudonymous identities are cycled
    through; `chatter_prefix` keeps two overlapping bursts from sharing them.
    """
    if count == 0:
        return []
    span = end - start
    step = span / count
    return [
        ChatSample(
            timestamp=at(start + step * index),
            chatter_hash=f"{chatter_prefix}-{index % chatters}",
            text=text,
            emote_count=emotes,
        )
        for index in range(count)
    ]


def calm_baseline(*, start: float = 0.0, end: float = 120.0) -> list[ChatSample]:
    """Ordinary conversation: a steady trickle from a handful of people."""
    return chat(start=start, end=end, count=24, chatters=8)


def hype_burst(
    *,
    start: float,
    end: float,
    chatter_prefix: str = "hype",
) -> list[ChatSample]:
    """A broad, emote-heavy reaction — the thing a clip is made of."""
    return chat(
        start=start,
        end=end,
        count=40,
        chatters=30,
        text=REACTION_TEXT,
        emotes=3,
        chatter_prefix=chatter_prefix,
    )


def ordered(*groups: Sequence[ChatSample]) -> list[ChatSample]:
    """Merge sample groups into one chronological list."""
    merged: list[ChatSample] = []
    for group in groups:
        merged.extend(group)
    merged.sort(key=lambda sample: sample.timestamp)
    return merged


@pytest.fixture
def quiet_stream() -> list[ChatSample]:
    """Stable low activity throughout. Nothing here deserves a clip."""
    return calm_baseline(start=0.0, end=180.0)


@pytest.fixture
def single_spike() -> list[ChatSample]:
    """A calm stretch, then one strong burst."""
    return ordered(
        calm_baseline(start=0.0, end=120.0),
        hype_burst(start=120.0, end=130.0),
        calm_baseline(start=130.0, end=200.0),
    )


@pytest.fixture
def two_distinct_moments() -> list[ChatSample]:
    """Two bursts, far enough apart that the cooldown has expired between them."""
    return ordered(
        calm_baseline(start=0.0, end=120.0),
        hype_burst(start=120.0, end=130.0, chatter_prefix="first"),
        calm_baseline(start=130.0, end=260.0),
        hype_burst(start=260.0, end=270.0, chatter_prefix="second"),
        calm_baseline(start=270.0, end=340.0),
    )
