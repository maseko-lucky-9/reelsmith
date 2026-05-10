"""Brand vocabulary replacement (W2.7).

Applied at caption-burn time only. The original transcript is never
modified — replacements live in the brand template's ``vocabulary``
JSON column ({"OpusClip": "ReelSmith", "ai": "AI"}) and run word-by-
word against the burnt caption text.

Case-insensitive match; case-preserving replacement (preserves the
case pattern of the source token).
"""
from __future__ import annotations

import re
from typing import Mapping


def _preserve_case(source: str, replacement: str) -> str:
    if not source:
        return replacement
    if source.isupper():
        return replacement.upper()
    if source[0].isupper() and source[1:].islower():
        return replacement[:1].upper() + replacement[1:].lower()
    if source.islower():
        return replacement.lower()
    return replacement


def apply_vocabulary(text: str, vocabulary: Mapping[str, str] | None) -> str:
    if not text or not vocabulary:
        return text

    # Sort longer keys first so 'ai chat' beats 'ai'.
    keys = sorted(vocabulary.keys(), key=lambda k: -len(k))
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b",
        re.IGNORECASE,
    )

    lower_map = {k.lower(): v for k, v in vocabulary.items()}

    def _sub(m: re.Match[str]) -> str:
        src = m.group(0)
        target = lower_map[src.lower()]
        return _preserve_case(src, target)

    return pattern.sub(_sub, text)
