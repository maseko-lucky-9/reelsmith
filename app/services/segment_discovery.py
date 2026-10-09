"""Stateless helpers for discovering clips in a source without chapters (FR-009).

The orchestrator owns the sequence (transcribe the whole source once, score
candidate windows, keep the best, render each as a chapter); this module only
holds the pure pieces it needs:

* ``rebase_words`` moves full-source word timings onto a clip window's own
  clock, so captions never get negative or out-of-range times;
* the ``<source stem>.words.json`` sidecar keeps the full-source transcript
  next to the source video, so a later single-clip re-render reuses it;
* ``select_discovered`` keeps segments relative to the best one, because the
  heuristic scores of ``LocalHeuristicProposer`` run low (about 5-40 on real
  speech) and any fixed bar would either keep everything or drop everything;
* ``segments_to_chapters`` gives the kept segments the same chapter shape the
  YouTube-chapter path builds, plus the score fields the clip row stores.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.domain.models import PipelineOptions
from app.services.segment_proposer import ProposedSegment, select_segments
from app.services.transcription_service import WordTiming
from app.settings import settings

log = logging.getLogger(__name__)

# Clips one discovery keeps at most (no per-job option exists yet).
DEFAULT_MAX_CLIPS = 5
# A segment is kept when it scores at least this share of the best segment.
MIN_SCORE_RATIO = 0.4
# Same floor the chapter path uses when it clamps chapters to the safe end.
MIN_CHAPTER_SECONDS = 0.5
_SIDECAR_SUFFIX = ".words.json"


def _field(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def rebase_words(words: Sequence[Any], start: float, end: float) -> list[WordTiming]:
    """Words of ``[start, end)`` on the window's clock (0 = ``start``).

    Words wholly outside the window are dropped (one that only touches an
    edge counts as outside); words straddling an edge are clamped to it, so
    every result has ``0 <= start <= end <= end - start``. Accepts
    ``WordTiming`` objects or ``{word, start, end}`` dicts; the input is not
    modified.
    """
    span = end - start
    out: list[WordTiming] = []
    for w in words:
        w_start = float(_field(w, "start") or 0.0)
        w_end = float(_field(w, "end") or w_start)
        if w_end <= start or w_start >= end:
            continue
        rel_start = min(max(w_start - start, 0.0), span)
        rel_end = min(max(w_end - start, rel_start), span)
        out.append(WordTiming(str(_field(w, "word") or ""), rel_start, rel_end))
    return out


def words_sidecar_path(video_path: str) -> Path:
    """``<source stem>.words.json`` next to the source video."""
    source = Path(video_path)
    return source.with_name(source.stem + _SIDECAR_SUFFIX)


def write_words_sidecar(video_path: str, words: Sequence[Any]) -> Path:
    """Atomically write the full-source words as ``[{word, start, end}, ...]``."""
    path = words_sidecar_path(video_path)
    data = [
        {
            "word": str(_field(w, "word") or ""),
            "start": float(_field(w, "start") or 0.0),
            "end": float(_field(w, "end") or 0.0),
        }
        for w in words
    ]
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def read_words_sidecar(video_path: str) -> list[WordTiming] | None:
    """The sidecar's words, or ``None`` when it is missing or unreadable."""
    path = words_sidecar_path(video_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("sidecar is not a list")
        return [
            WordTiming(str(d["word"]), float(d["start"]), float(d["end"])) for d in data
        ]
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("Ignoring unreadable words sidecar %s (%s)", path, e)
        return None


def relative_min_score(
    segments: Sequence[ProposedSegment], ratio: float = MIN_SCORE_RATIO
) -> int:
    """``floor(ratio * best score)``, never below 0 (0 for no segments)."""
    if not segments:
        return 0
    best = max(s.score for s in segments)
    return max(0, math.floor(ratio * best))


def select_discovered(
    segments: Sequence[ProposedSegment], max_clips: int = DEFAULT_MAX_CLIPS
) -> list[ProposedSegment]:
    """The best non-overlapping segments above the relative bar, by start."""
    return select_segments(
        list(segments), max_clips=max_clips, min_score=relative_min_score(segments)
    )


def clip_length_range(opts: PipelineOptions) -> tuple[int, int]:
    """Clip length bounds: the job's options, else the configured defaults."""
    lo = opts.target_length_min_seconds or settings.target_clip_seconds_min
    hi = opts.target_length_max_seconds or settings.target_clip_seconds_max
    return lo, max(lo, hi)


def segments_to_chapters(
    segments: Sequence[ProposedSegment], safe_end: float
) -> list[dict[str, Any]]:
    """Chapters (index, title, start, end) plus the clip's score fields.

    Windows are clamped to ``safe_end`` and dropped when less than
    ``MIN_CHAPTER_SECONDS`` remains; indices are dense, in input order.
    An untitled segment is called ``Clip N`` (its 1-based position).
    """
    chapters: list[dict[str, Any]] = []
    for seg in segments:
        start = max(0.0, float(seg.start))
        end = min(float(seg.end), safe_end)
        if end - start < MIN_CHAPTER_SECONDS:
            log.warning(
                "Dropping discovered segment %.2f-%.2f past the safe end %.2f",
                seg.start,
                seg.end,
                safe_end,
            )
            continue
        index = len(chapters)
        chapters.append(
            {
                "index": index,
                "title": seg.title.strip() or f"Clip {index + 1}",
                "start": start,
                "end": end,
                "virality_score": seg.score,
                "score_breakdown": dict(seg.score_breakdown),
                "summary": seg.summary,
            }
        )
    return chapters
