"""Test doubles for ``face_detector.FaceDetector`` and a synthetic "face" video.

* ``ScriptedDetector`` returns a fixed face list per call (in the detector's
  input pixels), cycling through its script.
* ``BlockDetector`` finds the white block of ``write_block_video`` in the
  frame it is given: a deterministic stand-in for a real face detector that
  still exercises decoding, scaling, rotation and time rebasing.

Both record the threads they ran on, so tests can prove detection never runs
on the event loop thread.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from app.services.face_detector import Face

BLOCK = 40  # side of the white block, source px
BACKGROUND = 30
WHITE = 255


class _Recording:
    def __init__(self) -> None:
        self.calls = 0
        self.threads: set[int] = set()
        self.shapes: list[tuple[int, ...]] = []

    def _record(self, image: np.ndarray) -> None:
        self.calls += 1
        self.threads.add(threading.get_ident())
        self.shapes.append(tuple(image.shape))


class ScriptedDetector(_Recording):
    """Returns ``script[call % len(script)]``; faces are in INPUT pixels."""

    def __init__(self, script: Sequence[Sequence[Face]]) -> None:
        super().__init__()
        self.script = [list(faces) for faces in script]

    def detect(self, bgr: np.ndarray) -> list[Face]:
        self._record(bgr)
        return list(self.script[(self.calls - 1) % len(self.script)])


class BlockDetector(_Recording):
    """One face: the bounding box of the near-white pixels, if any."""

    def __init__(self, score: float = 0.95) -> None:
        super().__init__()
        self.score = score

    def detect(self, bgr: np.ndarray) -> list[Face]:
        self._record(bgr)
        ys, xs = np.nonzero((bgr >= 200).all(axis=2))
        if xs.size == 0:
            return []
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        return [
            Face(
                x=float(x0),
                y=float(y0),
                w=float(x1 - x0),
                h=float(y1 - y0),
                score=self.score,
            )
        ]


class FailingDetector(_Recording):
    def detect(self, bgr: np.ndarray) -> list[Face]:
        self._record(bgr)
        raise RuntimeError("detector exploded")


def write_block_video(
    path: Path,
    *,
    block_left: Callable[[int], int],
    size: tuple[int, int] = (640, 360),
    frames: int = 72,
    fps: Fraction = Fraction(24),
    block_top: int | None = None,
) -> Path:
    """A dark ``size`` video with a white ``BLOCK`` px square at
    ``block_left(frame index)``, vertically centred unless ``block_top``."""
    width, height = size
    top = (height - BLOCK) // 2 if block_top is None else block_top
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.options = {"preset": "veryfast", "crf": "18", "g": "12"}
        for index in range(frames):
            rgb = np.full((height, width, 3), BACKGROUND, np.uint8)
            left = block_left(index)
            rgb[top : top + BLOCK, left : left + BLOCK] = WHITE
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.pts = index
            container.mux(stream.encode(frame))
        container.mux(stream.encode(None))
    return path
