"""Editor-driven timeline render (W1.12).

Consumes the ``ClipEdit.timeline`` JSON produced by the inline editor
(W1.2 router) and produces a render plan or — when MoviePy is
available — a real CompositeVideoClip.

This module is split into:

* ``build_render_plan(timeline, base_clip_path)`` — pure function that
  emits a deterministic dict describing what should be rendered.
  Tested in isolation; never touches MoviePy.

* ``render_with_moviepy(plan, output_path)`` — opt-in that imports
  MoviePy and writes a file. Skipped in CI / unit tests.

Track schema (mirrors W1.2 router):

    {
      "tracks": [
        { "kind": "video",        "items": [...] },
        { "kind": "caption",      "items": [...] },
        { "kind": "text-overlay", "items": [...] }
      ]
    }

Item shapes (validated structurally):

    video:        { start, end, src ('main' or absolute path), trim_start? }
    caption:      { start, end, text?, style? }
    text-overlay: { start, end, text, x, y, font_size?, color? }
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


class TimelineError(ValueError):
    pass


@dataclass(frozen=True)
class VideoItem:
    start: float
    end: float
    src: str
    trim_start: float = 0.0


@dataclass(frozen=True)
class CaptionItem:
    start: float
    end: float
    text: str
    style: str = "default"


@dataclass(frozen=True)
class TextOverlayItem:
    start: float
    end: float
    text: str
    x: float
    y: float
    font_size: int = 36
    color: str = "#ffffff"


@dataclass(frozen=True)
class RenderPlan:
    duration: float
    video: list[VideoItem] = field(default_factory=list)
    captions: list[CaptionItem] = field(default_factory=list)
    overlays: list[TextOverlayItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration": self.duration,
            "video": [v.__dict__ for v in self.video],
            "captions": [c.__dict__ for c in self.captions],
            "overlays": [o.__dict__ for o in self.overlays],
        }


def _f(value: Any, *, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise TimelineError(f"{name}: expected number, got {value!r}") from exc


def build_render_plan(timeline: dict[str, Any], base_clip_path: str) -> RenderPlan:
    """Validate and shape a timeline JSON into a RenderPlan."""
    if not isinstance(timeline, dict) or "tracks" not in timeline:
        raise TimelineError("timeline missing 'tracks' key")
    tracks = timeline.get("tracks") or []
    if not isinstance(tracks, list):
        raise TimelineError("timeline.tracks must be a list")

    video: list[VideoItem] = []
    captions: list[CaptionItem] = []
    overlays: list[TextOverlayItem] = []

    for tr in tracks:
        kind = tr.get("kind") if isinstance(tr, dict) else None
        items = tr.get("items") if isinstance(tr, dict) else None
        if kind not in {"video", "caption", "text-overlay"}:
            raise TimelineError(f"unknown track kind: {kind!r}")
        if not isinstance(items, list):
            raise TimelineError(f"track {kind!r} items must be a list")

        for it in items:
            if not isinstance(it, dict):
                raise TimelineError(f"{kind} item must be an object, got {type(it).__name__}")
            if kind == "video":
                src = it.get("src") or "main"
                video.append(VideoItem(
                    start=_f(it.get("start", 0), name="video.start"),
                    end=_f(it.get("end", 0), name="video.end"),
                    src=base_clip_path if src == "main" else str(src),
                    trim_start=_f(it.get("trim_start", 0), name="video.trim_start"),
                ))
            elif kind == "caption":
                captions.append(CaptionItem(
                    start=_f(it.get("start", 0), name="caption.start"),
                    end=_f(it.get("end", 0), name="caption.end"),
                    text=str(it.get("text", "")),
                    style=str(it.get("style", "default")),
                ))
            else:  # text-overlay
                overlays.append(TextOverlayItem(
                    start=_f(it.get("start", 0), name="overlay.start"),
                    end=_f(it.get("end", 0), name="overlay.end"),
                    text=str(it.get("text", "")),
                    x=_f(it.get("x", 0.5), name="overlay.x"),
                    y=_f(it.get("y", 0.1), name="overlay.y"),
                    font_size=int(it.get("font_size", 36)),
                    color=str(it.get("color", "#ffffff")),
                ))

    # Default to the rightmost end across all items.
    duration = 0.0
    for v in video:
        duration = max(duration, v.end)
    for c in captions:
        duration = max(duration, c.end)
    for o in overlays:
        duration = max(duration, o.end)
    if duration <= 0 and video:
        duration = max(duration, video[-1].end)

    # Sanity: each item's end must be > start.
    for kind_name, items in (("video", video), ("captions", captions), ("overlays", overlays)):
        for idx, it in enumerate(items):
            if it.end <= it.start:
                raise TimelineError(
                    f"{kind_name}[{idx}]: end ({it.end}) must be > start ({it.start})"
                )

    return RenderPlan(
        duration=duration, video=video, captions=captions, overlays=overlays
    )


def render_with_moviepy(plan: RenderPlan, output_path: str) -> str:  # pragma: no cover
    """Write a real composited mp4. Heavy; not exercised in unit tests."""
    from moviepy.editor import (
        CompositeVideoClip, TextClip, VideoFileClip, concatenate_videoclips,
    )

    clips: list = []
    for v in plan.video:
        c = VideoFileClip(v.src).subclip(v.trim_start, v.trim_start + (v.end - v.start))
        c = c.set_start(v.start)
        clips.append(c)
    base = concatenate_videoclips(clips, method="compose") if clips else None

    overlay_clips = []
    for o in plan.overlays:
        tc = (TextClip(o.text, fontsize=o.font_size, color=o.color)
              .set_start(o.start)
              .set_duration(o.end - o.start)
              .set_position((o.x, o.y), relative=True))
        overlay_clips.append(tc)

    if base is None:
        raise TimelineError("render plan has no video items")
    composed = (
        CompositeVideoClip([base, *overlay_clips]) if overlay_clips else base
    )
    composed.write_videofile(output_path, codec="libx264", audio_codec="aac")
    return output_path
