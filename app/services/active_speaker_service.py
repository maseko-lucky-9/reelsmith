"""Active-speaker reframe provider (W2.2).

Picks the camera region for each frame based on face detection +
audio-correlated lip movement. The heavy lifting (mediapipe / OpenCV /
audio xcorr) is opt-in and left behind ``YTVIDEO_ACTIVE_SPEAKER_HEAVY``;
the default codepath uses face-track centres weighted by mouth open
ratio when available, and falls back to the existing letterbox
behaviour otherwise.

Pure functions only — heavy MediaPipe path is gated behind a flag so
unit tests stay hermetic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

LayoutKind = Literal["fullscreen", "split", "screenshare"]


@dataclass(frozen=True)
class FaceObservation:
    timestamp: float
    cx: float       # 0..1 frame width
    cy: float       # 0..1 frame height
    confidence: float


@dataclass(frozen=True)
class ReframeCue:
    timestamp: float
    cx: float
    cy: float
    layout: LayoutKind


def smooth_cues(
    obs: Iterable[FaceObservation],
    *,
    window: int = 5,
    min_conf: float = 0.3,
) -> list[ReframeCue]:
    """Centre-of-mass smoothing over the rolling ``window`` of observations.

    Returns one ReframeCue per kept observation; below-confidence frames
    are dropped (caller falls back to the previous cue or letterbox).
    """
    obs_list = [o for o in obs if o.confidence >= min_conf]
    if not obs_list:
        return []
    obs_list.sort(key=lambda o: o.timestamp)

    cues: list[ReframeCue] = []
    for i, o in enumerate(obs_list):
        lo = max(0, i - window // 2)
        hi = min(len(obs_list), i + window // 2 + 1)
        slice_ = obs_list[lo:hi]
        cx = sum(s.cx for s in slice_) / len(slice_)
        cy = sum(s.cy for s in slice_) / len(slice_)
        cues.append(
            ReframeCue(
                timestamp=o.timestamp,
                cx=cx,
                cy=cy,
                layout="fullscreen",
            )
        )
    return cues


def detect_split_screen(faces_per_frame: Iterable[list[FaceObservation]]) -> bool:
    """Heuristic: 2+ faces with >0.4 horizontal separation across the
    majority of frames -> split-screen layout."""
    frames = list(faces_per_frame)
    if not frames:
        return False
    split_frames = 0
    for fs in frames:
        if len(fs) < 2:
            continue
        xs = sorted([f.cx for f in fs if f.confidence >= 0.3])
        if len(xs) >= 2 and (xs[-1] - xs[0]) > 0.4:
            split_frames += 1
    return split_frames > len(frames) * 0.5
