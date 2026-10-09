"""Face track → real render: the tracked subject stays inside the reel.

A synthetic source (a white block, the "face", moving right across a dark
640x360 frame) is decoded by ``reframe_service.get_crop_track`` with a
block-finding fake detector, and the track is rendered by the real bundled
ffmpeg. Every output frame must show the block where an independent oracle
puts it (source position minus the interpolated, even-floored crop edge,
scaled to the canvas) and fully inside the 9:16 window. The chapter starts at
0.5 s, so a track left in source time would be 0.5 s late.
"""

from __future__ import annotations

import av
import numpy as np
import pytest

from app.services import clip_service, reframe_service, render_service
from tests.unit.fake_face_detector import BLOCK, BlockDetector, write_block_video

pytestmark = pytest.mark.e2e

FPS = 24
FRAMES = 72
START, END = 0.5, 2.5
GEOMETRY = clip_service.reel_geometry(640, 360)
WINDOW = render_service.pan_crop(GEOMETRY)  # 202x360
SCALE = GEOMETRY.canvas_size[0] / WINDOW.width


def _block_left(index: int) -> int:
    return 60 + round(280 * index / (FRAMES - 1))  # ~94 px/s to the right


def _measured_centres(path) -> list[float | None]:
    centres: list[float | None] = []
    with av.open(str(path)) as container:
        for frame in container.decode(container.streams.video[0]):
            cols = np.nonzero((frame.to_ndarray(format="gray") >= 200).any(axis=0))[0]
            centres.append((cols.min() + cols.max() + 1) / 2 if cols.size else None)
    return centres


def test_tracked_block_stays_in_the_reel_where_the_track_puts_it(tmp_path):
    src = write_block_video(tmp_path / "src.mp4", block_left=_block_left, frames=FRAMES)

    plan = reframe_service.get_crop_track(src, START, END, detector=BlockDetector())
    assert plan.track is not None and len(plan.track) >= 2
    out = render_service.render_clip(
        str(src), str(tmp_path / "reel.mp4"), START, END,
        word_timings=[], crop_track=plan.track,
    )  # fmt: skip

    ts = [t for t, _ in plan.track]
    xs = [x for _, x in plan.track]
    measured = _measured_centres(out)
    assert len(measured) == round((END - START) * FPS)
    first = round(START * FPS)
    for k, got in enumerate(measured):
        block_cx = _block_left(first + k) + BLOCK / 2
        x_left = float(np.interp(k / FPS, ts, xs))
        x_even = x_left - x_left % 2  # crop rounds x down to an even column
        # The subject never leaves the window...
        assert x_left + BLOCK / 2 <= block_cx <= x_left + WINDOW.width - BLOCK / 2
        # ...and the render shows it where the track says, on the right frame.
        want = (block_cx - x_even) * SCALE
        assert got is not None and got == pytest.approx(want, abs=8), k
    assert xs[-1] - xs[0] > 100  # the window really panned
