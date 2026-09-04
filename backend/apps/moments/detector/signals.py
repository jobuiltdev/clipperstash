"""Signal extraction from a window of chat samples.

Everything here is counting. No scaling, no weighting, no decisions — those live
in the scorer, so the raw numbers stay inspectable and can be persisted as
diagnostics whatever the scoring formula later becomes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from apps.moments.detector.config import DEFAULT_CONFIG, DetectorConfig
from apps.moments.detector.window import ChatSample

# Tokens are runs of letters, digits, underscore or apostrophe. Splitting on
# this means punctuation is a boundary, so "what?!" yields "what" while
# "whatever" never does — matching is on whole tokens, never substrings.
TOKEN_PATTERN = re.compile(r"[a-z0-9_']+")

# Collapses a run of three or more identical characters to one, so "yoooo"
# becomes "yo" and "lolll" becomes "lol". Three is the floor deliberately:
# doubled letters are ordinary English ("cool", "wtff" is not), so only clear
# elongation is normalized.
ELONGATION_PATTERN = re.compile(r"(.)\1{2,}")


@dataclass(frozen=True)
class WindowSignals:
    """Raw counts for one window. No interpretation applied."""

    message_count: int
    unique_chatters: int
    emote_count: int
    messages_with_emotes: int
    reaction_message_count: int
    unique_reacting_chatters: int
    duration_seconds: float

    @property
    def messages_per_second(self) -> float:
        if self.duration_seconds <= 0:
            return 0.0
        return self.message_count / self.duration_seconds

    @property
    def emotes_per_message(self) -> float:
        if self.message_count == 0:
            return 0.0
        return self.emote_count / self.message_count

    @property
    def emote_coverage(self) -> float:
        """Share of messages carrying at least one emote."""
        if self.message_count == 0:
            return 0.0
        return self.messages_with_emotes / self.message_count

    @property
    def reaction_ratio(self) -> float:
        if self.message_count == 0:
            return 0.0
        return self.reaction_message_count / self.message_count

    @property
    def chatter_participation(self) -> float:
        """1.0 when every message came from a different person."""
        if self.message_count == 0:
            return 0.0
        return self.unique_chatters / self.message_count


def normalize_tokens(text: str, *, config: DetectorConfig = DEFAULT_CONFIG) -> list[str]:
    """Lowercase, split into tokens, and collapse deliberate elongation.

    Elongation is only collapsed for tokens the lexicon expects it on, so
    ordinary words with doubled letters are left alone.
    """
    tokens = TOKEN_PATTERN.findall(text.lower())

    normalized: list[str] = []
    for token in tokens:
        collapsed = ELONGATION_PATTERN.sub(r"\1", token)
        if collapsed != token and collapsed in config.elongatable_tokens:
            normalized.append(collapsed)
        else:
            normalized.append(token)
    return normalized


def has_reaction(text: str, *, config: DetectorConfig = DEFAULT_CONFIG) -> bool:
    """Whether a message reads as a crowd reaction.

    A whole-token match against the lexicon, or a run of consecutive tokens
    matching a phrase. Never a substring search.
    """
    tokens = normalize_tokens(text, config=config)
    if not tokens:
        return False

    if any(token in config.reaction_tokens for token in tokens):
        return True

    return any(_contains_phrase(tokens, phrase) for phrase in config.reaction_phrases)


def _contains_phrase(tokens: Sequence[str], phrase: Sequence[str]) -> bool:
    if not phrase or len(phrase) > len(tokens):
        return False
    span = len(phrase)
    return any(
        tuple(tokens[index : index + span]) == tuple(phrase)
        for index in range(len(tokens) - span + 1)
    )


def extract_signals(
    samples: Iterable[ChatSample],
    *,
    duration_seconds: float,
    config: DetectorConfig = DEFAULT_CONFIG,
) -> WindowSignals:
    """Count everything the scorer needs from one window, in a single pass."""
    message_count = 0
    emote_count = 0
    messages_with_emotes = 0
    reaction_message_count = 0
    chatters: set[str] = set()
    reacting_chatters: set[str] = set()

    for sample in samples:
        message_count += 1
        chatters.add(sample.chatter_hash)

        emotes = max(0, sample.emote_count)
        emote_count += emotes
        if emotes:
            messages_with_emotes += 1

        if has_reaction(sample.text, config=config):
            reaction_message_count += 1
            reacting_chatters.add(sample.chatter_hash)

    return WindowSignals(
        message_count=message_count,
        unique_chatters=len(chatters),
        emote_count=emote_count,
        messages_with_emotes=messages_with_emotes,
        reaction_message_count=reaction_message_count,
        unique_reacting_chatters=len(reacting_chatters),
        duration_seconds=duration_seconds,
    )
