"""Linear-time normalization with source offset preservation."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}
_PUNCTUATION = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "！": "!",
        "？": "?",
        "；": ";",
        "：": ":",
        "（": "(",
        "）": ")",
    }
)
_COMPACT_SEPARATORS = {" ", "\t", "\r", "\n", "·", "・", "_"}


@dataclass(frozen=True, slots=True)
class NormalizedPolicyText:
    raw: str
    normalized: str
    compact: str
    normalized_to_raw: tuple[int, ...]
    compact_to_normalized: tuple[int, ...]


def normalize_policy_text(value: object) -> NormalizedPolicyText:
    raw = value if isinstance(value, str) else str(value or "")
    if len(raw) > 20_000:
        raw = raw[:20_000]

    expanded: list[tuple[str, int]] = []
    for raw_index, raw_character in enumerate(raw):
        for character in unicodedata.normalize("NFKC", raw_character):
            if character in _ZERO_WIDTH:
                continue
            if unicodedata.category(character).startswith("C"):
                character = " "
            character = character.lower().translate(_PUNCTUATION)
            expanded.append((character, raw_index))

    normalized_characters: list[str] = []
    normalized_to_raw: list[int] = []
    previous_space = True
    for character, raw_index in expanded:
        if character.isspace():
            if previous_space:
                continue
            character = " "
            previous_space = True
        else:
            previous_space = False
        normalized_characters.append(character)
        normalized_to_raw.append(raw_index)
    if normalized_characters and normalized_characters[-1] == " ":
        normalized_characters.pop()
        normalized_to_raw.pop()

    compact_characters: list[str] = []
    compact_to_normalized: list[int] = []
    for index, character in enumerate(normalized_characters):
        if character in _COMPACT_SEPARATORS:
            continue
        compact_characters.append(character)
        compact_to_normalized.append(index)

    return NormalizedPolicyText(
        raw=raw,
        normalized="".join(normalized_characters),
        compact="".join(compact_characters),
        normalized_to_raw=tuple(normalized_to_raw),
        compact_to_normalized=tuple(compact_to_normalized),
    )
