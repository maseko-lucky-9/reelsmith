"""Where a reel's B-roll inserts go and what they show (FR-010, T012).

``plan_broll`` turns a clip's word timings (clip-relative seconds: 0 is the
first frame of the reel) into at most ``max_inserts`` windows of
``insert_seconds``, each with a one-word search query. Pure and
deterministic; the tokenizer and stopwords are the segment proposer's, the
rest is the standard library.

Rules:

* A window starts no earlier than ``MIN_START_SECONDS`` (the hook stays on
  the speaker) and ends no later than ``END_MARGIN_SECONDS`` before the clip
  end; windows never overlap and keep at least ``MIN_GAP_SECONDS`` between
  them. A clip shorter than 3 + 3 + 2 = 8 s gets no window.
* Candidate windows start where a word with a token starts, clamped into
  the allowed range. A word is inside a window when it STARTS inside it
  (half-open).
* A window's query is its best token: the longest one (ties: spoken first,
  then alphabetical). A token is a lowercase run of the proposer's
  ``_TOKEN_RE``, quotes and a possessive ``'s`` stripped, at least
  ``MIN_QUERY_CHARS`` letters, letters only (no numbers or contractions) and
  not one of the proposer's stopwords. A window without a token is skipped.
* Windows are ranked by query length, then earliest start, and taken
  greedily while they keep the spacing and their query is new (one insert
  per query). The plan is returned sorted by start.

Times are compared in whole microseconds, the precision ``validate_broll``
uses, so every plan passes the render's timing rules.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.services.segment_proposer import _STOPWORDS, _TOKEN_RE

MIN_START_SECONDS = 3.0
END_MARGIN_SECONDS = 2.0
MIN_GAP_SECONDS = 1.0
MIN_QUERY_CHARS = 4

_US = 1_000_000


@dataclass(frozen=True)
class PlannedBroll:
    """One insert window: ``[start, start + duration)`` clip seconds."""

    start: float
    duration: float
    query: str


def _us(seconds: float) -> int:
    return round(seconds * _US)


def _field(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def query_token(word: str) -> str | None:
    """The best query token of one transcribed word, or None."""
    best: str | None = None
    for token in _TOKEN_RE.findall(str(word or "").lower()):
        token = token.strip("'")
        if token.endswith("'s"):
            token = token[:-2]
        if len(token) < MIN_QUERY_CHARS or not token.isalpha():
            continue
        if token in _STOPWORDS:
            continue
        if best is None or len(token) > len(best):
            best = token
    return best


def _spoken_tokens(words: Sequence[Any]) -> list[tuple[int, str]]:
    """``(start µs, token)`` for every word with a query token, sorted."""
    out = []
    for w in words:
        start = _field(w, "start")
        try:
            start = float(start)
        except (TypeError, ValueError):
            continue
        token = query_token(_field(w, "word"))
        if token is not None and math.isfinite(start):
            out.append((_us(start), token))
    out.sort()
    return out


def _window_query(tokens: list[tuple[int, str]], lo: int, hi: int) -> str | None:
    """Longest token starting in ``[lo, hi)``; ties go to the first spoken."""
    best: str | None = None
    for start, token in tokens:
        if lo <= start < hi and (best is None or len(token) > len(best)):
            best = token
    return best


def plan_broll(
    words: Sequence[Any],
    duration: float,
    *,
    max_inserts: int = 2,
    insert_seconds: float = 3.0,
) -> list[PlannedBroll]:
    """At most ``max_inserts`` B-roll windows for a clip of ``duration`` s.

    ``words`` are ``WordTiming`` objects or ``{word, start, end}`` dicts on
    the clip's clock. See the module docstring for the rules. Raises
    ``ValueError`` when ``insert_seconds`` is not a positive finite number.
    """
    if not (math.isfinite(insert_seconds) and insert_seconds > 0):
        raise ValueError(f"insert_seconds must be > 0, got {insert_seconds}")
    if max_inserts <= 0 or not math.isfinite(duration):
        return []
    length = _us(insert_seconds)
    first = _us(MIN_START_SECONDS)
    last = _us(duration) - _us(END_MARGIN_SECONDS) - length
    if last < first:
        return []
    tokens = _spoken_tokens(words)
    starts = sorted({min(max(start, first), last) for start, _token in tokens})

    candidates = []
    for start in starts:
        query = _window_query(tokens, start, start + length)
        if query is not None:
            candidates.append((start, query))
    candidates.sort(key=lambda c: (-len(c[1]), c[0]))

    gap = _us(MIN_GAP_SECONDS)
    chosen: list[tuple[int, str]] = []
    for start, query in candidates:
        if len(chosen) == max_inserts:
            break
        if any(query == q for _s, q in chosen):
            continue
        if all(
            start + length + gap <= other or other + length + gap <= start
            for other, _q in chosen
        ):
            chosen.append((start, query))
    chosen.sort()
    return [PlannedBroll(start / _US, length / _US, query) for start, query in chosen]
