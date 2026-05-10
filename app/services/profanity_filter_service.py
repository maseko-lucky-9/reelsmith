"""Profanity filter for caption-burn time (W2.9).

Replaces any filtered word with bleep characters. Operates on the
final caption string only — never on the transcript stored in the
database.

Settings:
    YTVIDEO_PROFANITY_FILTER  off | default | custom
    YTVIDEO_PROFANITY_WORDS   comma-separated custom list (mode=custom)
"""
from __future__ import annotations

import re
from typing import Iterable

# Conservative default list. The plan deliberately keeps this short;
# operators wanting more aggressive filtering should set mode=custom.
DEFAULT_LIST: tuple[str, ...] = (
    "fuck", "fucking", "shit", "bitch", "asshole",
    "bullshit", "dick",
)


def _bleep(word: str) -> str:
    if len(word) <= 1:
        return word
    # Preserve first letter for readability.
    return word[0] + ("*" * (len(word) - 1))


def filter_text(
    text: str,
    *,
    mode: str = "default",
    custom_words: Iterable[str] = (),
) -> str:
    if mode == "off":
        return text

    if mode == "custom":
        wordlist = {w.lower() for w in custom_words if w.strip()}
    elif mode == "default":
        wordlist = set(DEFAULT_LIST)
    else:
        raise ValueError(f"unknown profanity filter mode: {mode!r}")

    if not wordlist:
        return text

    # Build a single boundary-anchored regex that matches any listed
    # word case-insensitively while preserving surrounding whitespace
    # and punctuation.
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(w) for w in sorted(wordlist, key=len, reverse=True))
        + r")\b",
        re.IGNORECASE,
    )

    def _sub(m: re.Match[str]) -> str:
        word = m.group(0)
        return _bleep(word)

    return pattern.sub(_sub, text)
