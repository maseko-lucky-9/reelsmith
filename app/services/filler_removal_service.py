"""Filler / silence removal (W2.5).

Lexicon-based approach for words; VAD-based approach for silences.
Pure-function planner returns the list of (start, end) intervals to
KEEP. The render stage (or moviepy concatenator) consumes these and
splices them together.

The heavy webrtcvad-based silence detector is opt-in; the default
pure-Python heuristic uses word-end gaps which is enough for short
clips.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

DEFAULT_FILLERS: tuple[str, ...] = (
    "um", "uh", "uhm", "ah", "er", "erm",
    "like", "you know", "i mean",
    "sort of", "kind of", "basically", "literally",
)


@dataclass(frozen=True)
class WordSpan:
    text: str
    start: float
    end: float


def _is_filler(word: str, allowlist: set[str]) -> bool:
    return word.lower().strip(",.!?;:'\"") in allowlist


def plan_keep_intervals(
    words: Iterable[WordSpan],
    *,
    filler_words: Iterable[str] = DEFAULT_FILLERS,
    max_silence_seconds: float = 0.6,
    pad_seconds: float = 0.05,
) -> list[tuple[float, float]]:
    """Return the (start, end) intervals to KEEP after dropping fillers + long silences.

    Adjacent kept words are merged. Each retained span is padded by
    ``pad_seconds`` on either side, clamped against the next/prev
    boundary.
    """
    allowlist = {f.lower() for f in filler_words}
    word_list = sorted(list(words), key=lambda w: w.start)
    if not word_list:
        return []

    keepers: list[WordSpan] = [w for w in word_list if not _is_filler(w.text, allowlist)]
    if not keepers:
        return []

    # Coalesce: any inter-keeper silence > max_silence_seconds is dropped.
    intervals: list[tuple[float, float]] = []
    cur_start = keepers[0].start
    cur_end = keepers[0].end
    for w in keepers[1:]:
        gap = w.start - cur_end
        if gap > max_silence_seconds:
            intervals.append((cur_start, cur_end))
            cur_start = w.start
        cur_end = w.end
    intervals.append((cur_start, cur_end))

    # Apply padding without overlap.
    padded: list[tuple[float, float]] = []
    for i, (s, e) in enumerate(intervals):
        prev_end = padded[-1][1] if padded else 0.0
        next_start = intervals[i + 1][0] if i + 1 < len(intervals) else float("inf")
        ps = max(prev_end, s - pad_seconds)
        pe = min(next_start, e + pad_seconds)
        if pe > ps:
            padded.append((round(ps, 4), round(pe, 4)))
    return padded


def total_kept(intervals: list[tuple[float, float]]) -> float:
    return sum(e - s for s, e in intervals)
