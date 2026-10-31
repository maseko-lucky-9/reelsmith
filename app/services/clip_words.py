"""Per-clip word timings for the editor (spec 016, T126).

Each rendered clip gets a words sidecar: the words of the clip on the clip's
own clock, 0 = the first frame of the reel, so they line up with the editor's
``video.currentTime``. It lives in a dedicated folder beside the clips folder,
``<job folder>/.clip_words/<clip stem>.words.json`` (``clip_words_path``), so
the clips folder holds only reels and thumbnails, an export (which copies the
reel files it is given) never takes it along, and a job delete (which removes
the whole job folder) removes it. Written with
``segment_discovery.write_words_sidecar``.

The orchestrator writes it on every render, re-renders included, so it is
never stale (a render without transcription removes it). A clip rendered
before this has none, and ``backfill_clip_words`` transcribes the clip file
once (on the first editor open) and caches it.

``default_timeline`` turns the words into the editor's starting timeline
(v1 shape): the whole reel on the video track and one caption item per
``words_per_caption`` words, each carrying its words' timings (``words``).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence
from pathlib import Path, PurePath
from typing import Any

from app.services import segment_discovery, transcription_service
from app.services.transcription_service import WordTiming

log = logging.getLogger(__name__)

CLIP_WORDS_DIRNAME = ".clip_words"
"""The words folder, a sibling of the clips folder inside the job folder."""

BACKFILL_GRACE_SECONDS = 30.0
"""Added to the decode budget to bound a whole backfill (model load, waiting
for another decode): the request answers with no words after that."""

MIN_CAPTION_SECONDS = 0.01
"""A caption item of zero-length words still spans this much (end > start)."""


def clip_words_path(clip_path: str | None) -> Path | None:
    """``<job folder>/.clip_words/<clip stem>.words.json`` for the reel at
    ``clip_path`` (``<job folder>/clips/<name>.mp4``), or ``None`` when it may
    not be used.

    Refused: a path with a NUL byte, a relative path, a ``..`` component, and
    a sidecar that resolves anywhere but directly inside the job folder's
    words folder (a symlinked folder or file, the filesystem root). The
    stem comes from the clip's file name only, so it holds no separator.
    """
    if not clip_path or "\x00" in clip_path or not os.path.isabs(clip_path):
        return None
    if ".." in PurePath(clip_path).parts:
        return None
    clip = Path(clip_path)
    job_folder = clip.parent.parent
    folder = job_folder / CLIP_WORDS_DIRNAME
    path = segment_discovery.words_sidecar_path(str(folder / clip.name))
    try:
        # A symlinked words folder or sidecar resolves elsewhere: refused.
        inside = path.resolve().parent == job_folder.resolve() / CLIP_WORDS_DIRNAME
    except (OSError, RuntimeError, ValueError):
        return None
    return path if inside else None


def clip_window_words(words: Sequence[Any], duration: float) -> list[WordTiming]:
    """``words`` on the clip's clock, clipped to ``[0, duration)``; unchanged
    (bar the type) when ``duration`` is not positive."""
    if duration > 0:
        return segment_discovery.rebase_words(words, 0.0, duration)
    return segment_discovery.rebase_words(words, 0.0, float("inf"))


def read_clip_words(clip_path: str) -> list[WordTiming] | None:
    """The clip's cached words, or ``None`` when there is no readable sidecar."""
    path = clip_words_path(clip_path)
    if path is None:
        return None
    # read_words_sidecar names its file after a video path's stem.
    return segment_discovery.read_words_sidecar(str(path.parent / Path(clip_path).name))


def write_clip_words(clip_path: str, words: Sequence[Any]) -> Path | None:
    """Cache the clip's words (clip clock); the sidecar's path, or ``None``
    when ``clip_words_path`` refuses the clip's path (nothing is written)."""
    path = clip_words_path(clip_path)
    if path is None:
        log.warning("Clip words not cached: refused path for clip %r", clip_path)
        return None
    path.parent.mkdir(exist_ok=True)
    # write_words_sidecar names its file after a video path's stem.
    return segment_discovery.write_words_sidecar(str(path.parent / Path(clip_path).name), words)


def clear_clip_words(clip_path: str) -> None:
    """Remove the clip's cached words, if any (a render made none)."""
    path = clip_words_path(clip_path)
    if path is not None:
        path.unlink(missing_ok=True)


async def backfill_clip_words(clip_path: str, *, duration: float, language: str | None) -> list[WordTiming] | None:
    """Transcribe the rendered clip and cache its words; ``None`` on failure.

    The clip file is the clip window, so its word times are already on the
    clip's clock. Bounded by the decode budget plus ``BACKFILL_GRACE_SECONDS``;
    a timeout or any error is logged and gives ``None`` with nothing cached,
    so the next open tries again. Cancellation propagates (and stops the
    decode at its next segment).
    """
    budget = transcription_service.decode_timeout_seconds(max(duration, 0.0))
    try:
        async with asyncio.timeout(budget + BACKFILL_GRACE_SECONDS):
            raw = await transcription_service.transcribe_words_async(
                clip_path, language=language, audio_duration_s=max(duration, 0.0)
            )
        words = clip_window_words(raw, duration)
        await asyncio.to_thread(write_clip_words, clip_path, words)
    except Exception as exc:  # noqa: BLE001 - the editor opens without words
        log.warning("Clip words backfill failed  clip=%s  (%s)", clip_path, exc)
        return None
    log.info("Clip words backfilled  clip=%s  words=%d", clip_path, len(words))
    return words


def words_to_dicts(words: Sequence[Any]) -> list[dict[str, Any]]:
    return [{"word": str(w.word), "start": float(w.start), "end": float(w.end)} for w in words]


def default_timeline(words: Sequence[Any], duration: float, words_per_caption: int) -> dict[str, Any]:
    """The editor's starting timeline for a clip with no saved edit.

    Video: the whole reel (``src: main``). Caption: ``words_per_caption``
    words per item (the grouping the render burns in), ``text`` their words
    joined, ``words`` their per-word timings. No text overlays.
    """
    items = words_to_dicts(words)
    n = max(1, int(words_per_caption))
    reel_end = duration if duration > 0 else max((w["end"] for w in items), default=0.0)
    captions: list[dict[str, Any]] = []
    for i in range(0, len(items), n):
        group = items[i : i + n]
        start = group[0]["start"]
        end = max(group[-1]["end"], start + MIN_CAPTION_SECONDS)
        captions.append(
            {
                "start": start,
                "end": end,
                "text": " ".join(w["word"] for w in group),
                "style": "default",
                "words": group,
            }
        )
    video_items = [{"start": 0.0, "end": reel_end, "src": "main", "trim_start": 0.0}] if reel_end > 0 else []
    return {
        "tracks": [
            {"kind": "video", "items": video_items},
            {"kind": "caption", "items": captions},
            {"kind": "text-overlay", "items": []},
        ]
    }
