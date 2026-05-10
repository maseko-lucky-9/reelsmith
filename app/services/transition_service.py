"""Per-cut transitions (W2.6).

Returns argv fragments suitable for ffmpeg's `xfade` filter so the
render stage can splice between two clips. Pure functions; no
subprocess invocation here.

Supported transitions:
    fade | slide-left | slide-right | zoom

A 'cut' (no transition) is the default — callers don't need this
service unless they want crossfade-style transitions.
"""
from __future__ import annotations

from typing import Literal

TransitionKind = Literal["fade", "slide-left", "slide-right", "zoom"]
SUPPORTED: tuple[TransitionKind, ...] = ("fade", "slide-left", "slide-right", "zoom")

# Map our names to ffmpeg xfade transition keywords.
_FFMPEG: dict[TransitionKind, str] = {
    "fade": "fade",
    "slide-left": "slideleft",
    "slide-right": "slideright",
    "zoom": "zoomin",
}


def xfade_filter(kind: TransitionKind, *, duration: float, offset: float) -> str:
    """Return the ``-filter_complex`` argument fragment for a single
    crossfade between two inputs labelled [0:v] and [1:v]."""
    if kind not in _FFMPEG:
        raise ValueError(f"unknown transition: {kind!r}; pick one of {SUPPORTED}")
    if duration <= 0:
        raise ValueError("transition duration must be > 0")
    name = _FFMPEG[kind]
    return (
        f"[0:v][1:v]xfade=transition={name}:"
        f"duration={duration:.3f}:offset={offset:.3f}[v]"
    )
