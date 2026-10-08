"""Caption overlay track for the one-pass ffmpeg render.

The legacy MoviePy renderer composited one full-canvas RGBA ``ImageClip`` per
caption word (~66 MB each at 1920x3413, all alive at once). Here every unique
``(text, highlight)`` caption is drawn ONCE by the unchanged
``subtitle_image_service.create_subtitle_image`` and fed to ffmpeg as a single
image-sequence input described by an ffconcat list:

* every PNG is cropped to ONE shared rectangle — the union of all captions'
  alpha bounding boxes, expanded outward to even x/y/w/h. Expanding only adds
  transparent pixels, so the composited result is pixel-identical to
  overlaying the full canvas, while 4:2:0 chroma stays aligned;
* the list uses filenames relative to its own directory (no escaping of user
  paths), ``option framerate 1000`` per entry (start times exact to the
  millisecond instead of the image demuxer's 1/25 s), the transparent blank
  between captions, and ends with the blank, repeated as the final entry, so
  the last caption is cleared at its scheduled end.
"""

from __future__ import annotations

import heapq
import threading
import logging
import os
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from app.services import ffmpeg_tools
from app.services.clip_service import CaptionEntry, ReelGeometry
from app.services.subtitle_image_service import create_subtitle_image

log = logging.getLogger(__name__)

CaptionKey = tuple[str, int | None]

_US = 1_000_000
# Duration given to the trailing blank when the last caption reaches the end.
_MIN_TAIL_US = 1_000
_PNG_COMPRESS_LEVEL = 1
# Captions drawn in parallel. Each create_subtitle_image call peaks at ~130 MB
# on a 1920x3413 canvas; measured for 80 unique captions: 1 worker 4.1 s /
# 275 MiB, 2 workers 2.4 s / 454 MiB, 4 workers 1.5 s / 730 MiB peak RSS.
_CAPTION_WORKERS = 2


@dataclass(frozen=True)
class Segment:
    """``key``'s caption (``None`` = blank) shown over ``[start_us, end_us)``."""

    key: CaptionKey | None
    start_us: int
    end_us: int


@dataclass(frozen=True)
class CaptionTrack:
    """Files written by ``build_caption_track`` (names relative to its dir)."""

    list_name: str
    blank: str
    images: dict[CaptionKey, str]
    x: int
    y: int
    width: int
    height: int


def even_union_rect(
    boxes: Iterable[tuple[int, int, int, int]],
) -> tuple[int, int, int, int]:
    """Union of ``(x0, y0, x1, y1)`` boxes, expanded outward to even coordinates.

    Left/top round down and right/bottom round up, so the result contains
    every box and its width and height are even.
    """
    boxes = list(boxes)
    if not boxes:
        raise ValueError("even_union_rect needs at least one box")
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    x0 -= x0 % 2
    y0 -= y0 % 2
    x1 += x1 % 2
    y1 += y1 % 2
    return x0, y0, x1, y1


def caption_timeline(
    entries: Sequence[CaptionEntry], total_duration: float
) -> list[Segment]:
    """Flatten a caption schedule into back-to-back segments from t=0.

    Boundaries are integer microseconds taken from each entry's own start and
    ``start + duration``. Entries with non-positive duration never show; all
    are clipped to ``[0, total_duration)``. Where entries overlap, the one
    listed LATER wins (it was the top MoviePy layer). Gaps are blank
    (``key=None``); the timeline ends at the last visible caption's end.
    """
    end_us = round(total_duration * _US)
    spans: list[tuple[int, int, int, CaptionKey]] = []
    for idx, entry in enumerate(entries):
        if entry.duration <= 0:
            continue
        s = max(0, round(entry.start * _US))
        e = min(end_us, round((entry.start + entry.duration) * _US))
        if e > s:
            spans.append((s, e, idx, (entry.text, entry.highlight)))
    if not spans:
        return []

    boundaries = sorted({0, *(s for s, *_ in spans), *(e for _, e, *_ in spans)})
    by_start = sorted(spans)
    active: list[tuple[int, int, CaptionKey]] = []  # (-idx, end, key) max-heap on idx
    nxt = 0
    segments: list[Segment] = []
    for a, b in zip(boundaries, boundaries[1:]):
        while nxt < len(by_start) and by_start[nxt][0] <= a:
            s, e, idx, key = by_start[nxt]
            heapq.heappush(active, (-idx, e, key))
            nxt += 1
        while active and active[0][1] <= a:
            heapq.heappop(active)
        key = active[0][2] if active else None
        if segments and segments[-1].key == key:
            segments[-1] = Segment(key, segments[-1].start_us, b)
        else:
            segments.append(Segment(key, a, b))
    while segments and segments[-1].key is None:
        segments.pop()
    return segments


def _quote(name: str) -> str:
    """ffconcat single-quoted string (``'`` becomes ``'\\''``)."""
    return "'" + name.replace("'", "'\\''") + "'"


def _seconds(us: int) -> str:
    return f"{us // _US}.{us % _US:06d}"


def write_ffconcat(
    path: str | Path,
    segments: Sequence[Segment],
    names: dict[CaptionKey, str],
    blank: str,
    total_duration: float,
) -> Path:
    """Write the caption ffconcat list for ``segments`` to ``path``.

    ``names`` / ``blank`` are file names relative to ``path``'s directory.
    """
    lines = ["ffconcat version 1.0"]

    def entry(name: str, duration_us: int | None) -> None:
        lines.append(f"file {_quote(name)}")
        lines.append("option framerate 1000")
        if duration_us is not None:
            lines.append(f"duration {_seconds(duration_us)}")

    for seg in segments:
        entry(blank if seg.key is None else names[seg.key], seg.end_us - seg.start_us)
    last_end = segments[-1].end_us if segments else 0
    entry(blank, max(round(total_duration * _US) - last_end, _MIN_TAIL_US))
    entry(blank, None)
    path = Path(path)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ffmpeg_tools.FfmpegCancelled(
            "caption drawing cancelled", returncode=None, stderr_tail=""
        )


def _draw_own_crop(
    key: CaptionKey,
    geometry: ReelGeometry,
    path: Path,
    cancel: threading.Event | None,
) -> tuple[int, int, int, int] | None:
    """Pass 1: draw one caption, write the crop of its own alpha bbox to
    ``path`` and return that bbox (``None`` and no file if fully transparent).

    Nothing but the bbox outlives the call: the full canvas (26 MB at
    1920x3413) and the crop are dropped before returning.
    """
    _check(cancel)
    text, highlight = key
    full = create_subtitle_image(
        text,
        geometry.canvas_size,
        highlight_word_index=highlight,
        text_anchor_y=geometry.band_anchor_y,
    )
    alpha = full[..., 3]
    rows = np.flatnonzero(alpha.any(axis=1))
    if rows.size == 0:
        return None
    cols = np.flatnonzero(alpha.any(axis=0))
    x0, y0, x1, y1 = int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1
    Image.fromarray(np.ascontiguousarray(full[y0:y1, x0:x1]), mode="RGBA").save(
        path, compress_level=_PNG_COMPRESS_LEVEL
    )
    return x0, y0, x1, y1


def build_caption_track(
    entries: Sequence[CaptionEntry],
    geometry: ReelGeometry,
    total_duration: float,
    out_dir: str | Path,
    *,
    cancel: threading.Event | None = None,
) -> CaptionTrack | None:
    """Render the unique captions, crop them to the shared rect and write the list.

    Two streaming passes keep memory O(1) in the number of unique captions:
    pass 1 draws each caption and writes the crop of its own bounding box to
    disk (only the bbox is kept); pass 2 re-opens each crop, pads it into the
    shared even rectangle and writes the final PNG. ``cancel`` (default: the
    event installed by ``ffmpeg_tools.to_thread_cancellable``) is checked
    between captions.

    Returns ``None`` (writing nothing) when no caption is visible in
    ``[0, total_duration)``.
    """
    segments = caption_timeline(entries, total_duration)
    if not segments:
        return None
    cancel = cancel if cancel is not None else ffmpeg_tools.current_cancel_event()
    out_dir = Path(out_dir)
    keys = list(dict.fromkeys(s.key for s in segments if s.key is not None))
    raws = [out_dir / f".raw_{n:04d}.png" for n in range(len(keys))]
    names: dict[CaptionKey, str] = {}
    try:
        workers = max(1, min(len(keys), os.cpu_count() or 1, _CAPTION_WORKERS))
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            bboxes = list(
                pool.map(
                    lambda job: _draw_own_crop(job[0], geometry, job[1], cancel),
                    zip(keys, raws),
                )
            )
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

        boxes = [bbox for bbox in bboxes if bbox is not None]
        x0, y0, x1, y1 = even_union_rect(boxes) if boxes else (0, 0, 2, 2)
        size = (x1 - x0, y1 - y0)

        for n, (key, bbox, raw) in enumerate(zip(keys, bboxes, raws)):
            _check(cancel)
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            if bbox is not None:
                with Image.open(raw) as crop:
                    canvas.paste(crop, (bbox[0] - x0, bbox[1] - y0))
                raw.unlink()
            name = f"cap_{n:04d}.png"
            canvas.save(out_dir / name, compress_level=_PNG_COMPRESS_LEVEL)
            names[key] = name
    finally:
        for raw in raws:
            raw.unlink(missing_ok=True)
    blank = "blank.png"
    Image.new("RGBA", size, (0, 0, 0, 0)).save(
        out_dir / blank, compress_level=_PNG_COMPRESS_LEVEL
    )
    list_name = "captions.ffconcat"
    write_ffconcat(out_dir / list_name, segments, names, blank, total_duration)
    log.info(
        "Caption track: %d segment(s), %d unique image(s), rect=%dx%d+%d+%d",
        len(segments),
        len(keys),
        size[0],
        size[1],
        x0,
        y0,
    )
    return CaptionTrack(
        list_name=list_name,
        blank=blank,
        images=names,
        x=x0,
        y=y0,
        width=size[0],
        height=size[1],
    )
