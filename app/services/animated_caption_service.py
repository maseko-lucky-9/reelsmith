"""Animated caption renderer (W2.1).

Five preset styles plus a `static` passthrough. Each style is described
by a dataclass with the parameters needed by the per-frame renderer:
animation kind, font, colours, stroke. The actual frame composition is
done by ``render_caption_frames`` which returns a list of (timestamp,
PIL.Image) pairs — fed into MoviePy as a transparent overlay clip in
production.

Tests assert on the descriptor + frame count + per-frame size, never
on pixel hashes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

ANIMATION_KINDS = (
    "static",
    "hormozi",
    "mrbeast",
    "karaoke",
    "boldpop",
    "subtle",
)


@dataclass(frozen=True)
class CaptionStylePreset:
    name: str
    animation_kind: str
    font_size: int = 42
    primary_color: str = "#ffffff"
    highlight_color: str | None = None
    stroke_color: str | None = "#000000"


PRESETS: dict[str, CaptionStylePreset] = {
    "static": CaptionStylePreset(
        name="static", animation_kind="static",
        font_size=42, primary_color="#ffffff",
    ),
    "hormozi": CaptionStylePreset(
        name="hormozi", animation_kind="hormozi",
        font_size=56, primary_color="#ffffff",
        highlight_color="#fff200",
    ),
    "mrbeast": CaptionStylePreset(
        name="mrbeast", animation_kind="mrbeast",
        font_size=64, primary_color="#ffffff",
        highlight_color="#ff0066",
    ),
    "karaoke": CaptionStylePreset(
        name="karaoke", animation_kind="karaoke",
        font_size=48, primary_color="#ffffff",
        highlight_color="#22d3ee",
    ),
    "boldpop": CaptionStylePreset(
        name="boldpop", animation_kind="boldpop",
        font_size=60, primary_color="#ffffff",
        highlight_color="#7c3aed",
    ),
    "subtle": CaptionStylePreset(
        name="subtle", animation_kind="subtle",
        font_size=36, primary_color="#ffffff",
    ),
}


@dataclass(frozen=True)
class CaptionWord:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class CaptionFrame:
    timestamp: float
    text: str
    highlighted_word_index: int | None
    style: CaptionStylePreset


def get_preset(name: str) -> CaptionStylePreset:
    if name not in PRESETS:
        raise ValueError(
            f"unknown caption style: {name!r}; must be one of {sorted(PRESETS)}"
        )
    return PRESETS[name]


def plan_caption_frames(
    words: Iterable[CaptionWord],
    *,
    style: str = "hormozi",
    fps: int = 30,
) -> list[CaptionFrame]:
    """Deterministic 'render plan' for animated captions.

    Returns one frame per ``1/fps`` interval covering [first_word.start,
    last_word.end]. Each frame carries the caption text shown at that
    instant + the index of the currently-spoken word for highlight.
    """
    preset = get_preset(style)
    word_list = list(words)
    if not word_list:
        return []

    start = min(w.start for w in word_list)
    end = max(w.end for w in word_list)
    if end <= start:
        return []

    frame_dt = 1.0 / fps
    frames: list[CaptionFrame] = []
    t = start
    while t < end:
        # Show all words spoken up to (but not past) ``t + frame_dt``.
        visible: list[CaptionWord] = [w for w in word_list if w.start <= t]
        if not visible:
            t += frame_dt
            continue

        active_idx = None
        for i, w in enumerate(visible):
            if w.start <= t < w.end:
                active_idx = i
                break

        text = " ".join(w.text for w in visible[-7:])  # rolling window
        frames.append(
            CaptionFrame(
                timestamp=round(t, 4),
                text=text,
                highlighted_word_index=(
                    active_idx if active_idx is not None else None
                ),
                style=preset,
            )
        )
        t += frame_dt
    return frames
