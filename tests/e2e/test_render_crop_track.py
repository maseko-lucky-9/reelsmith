"""Real renders with a moving crop track through the bundled ffmpeg.

The P0 sync fixture's bit block sits top-left, outside any centred crop
window, and ``sync_checker`` reads it at one static output position. So these
tests generate their own variants of the 640x360 fixture into ``tmp_path``
(same frames, click track and encoder settings, via the fixture generator's
own drawing helpers) with the bit block drawn where the crop window will be:

* pinned  — block centred in the source frame, crop window centred on it;
* moving  — block drawn, frame by frame, at the centre of where the track
  says the crop window is. The render then shows it at ONE static output
  position only if ffmpeg pans the window by the track at the right time;
  a constant or mistimed crop pushes it off the sampled cells.

A separate horizontal-ramp source checks the pan direction from pixel
values, so a no-op crop cannot pass.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path

import av
import numpy as np
import pytest

from app.services import clip_service, ffmpeg_tools, render_service
from tests.fixtures.make_sync_fixture import (
    AUDIO_RATE,
    CLICK_FRAMES_640,
    FPS,
    FRAME_COUNT_640,
    _click_track,
    _draw_frame,
)
from tests.sync_checker import BlockGeometry, assert_av_sync, read_frame_indices

pytestmark = pytest.mark.e2e

SRC_W, SRC_H = 640, 360
GEOMETRY = clip_service.reel_geometry(SRC_W, SRC_H)  # canvas 640x1137
WINDOW = render_service.pan_crop(GEOMETRY)  # 202x360 at y=0
MAX_X = SRC_W - WINDOW.width  # 438
CELL = SRC_W // 32  # 20 px, as in the P0 fixture
BLOCK_W, BLOCK_H = CELL * 8, CELL * 2
BLOCK_IN_WINDOW = (WINDOW.width - BLOCK_W) // 2  # 21: block centred in the window
BLOCK_Y = (SRC_H - BLOCK_H) // 2  # 160: vertically centred

START, DURATION = 1.01, 3.0  # same chapter as test_render_sync's 640 render
FIRST_FRAME = math.ceil(START * FPS)  # 25: the first kept frame is output t=0

# Output position of the block: the window scaled to fill the 640x1137 canvas.
_SX = GEOMETRY.canvas_size[0] / WINDOW.width
_SY = GEOMETRY.canvas_size[1] / WINDOW.height
OUTPUT_BLOCK = BlockGeometry(
    x=BLOCK_IN_WINDOW, y=BLOCK_Y - WINDOW.y, cell_w=CELL, cell_h=CELL
).transformed(_SX, _SY, 0, 0)

MOVING_TRACK = [(0.0, 20.0), (1.2, 420.0), (1.8, 420.0), (2.9, 150.0)]
PINNED_TRACK = [(0.0, (SRC_W - WINDOW.width) // 2)]  # 219: centred window


def _track_x(track, t: float) -> float:
    """Independent oracle for the crop's left edge at chapter time ``t``."""
    ts = [k[0] for k in track]
    xs = np.clip([k[1] for k in track], 0, MAX_X)
    return float(np.interp(t, ts, xs))


def _write_source(
    path: Path, block_left: Callable[[int], int], *, with_audio: bool = True
) -> Path:
    """The P0 640x360 fixture with the bit block at ``block_left(frame)``."""
    with av.open(str(path), "w") as container:
        video = container.add_stream("libx264", rate=FPS)
        video.width, video.height, video.pix_fmt = SRC_W, SRC_H, "yuv420p"
        video.options = {"preset": "veryfast", "crf": "18", "bf": "3", "g": "48"}
        audio = None
        if with_audio:
            audio = container.add_stream("aac", rate=AUDIO_RATE)
            audio.layout = "mono"
            audio.bit_rate = 128_000
        for index in range(FRAME_COUNT_640):
            geometry = BlockGeometry(
                x=block_left(index), y=BLOCK_Y, cell_w=CELL, cell_h=CELL
            )
            frame = av.VideoFrame.from_ndarray(
                _draw_frame(index, SRC_W, SRC_H, geometry), format="rgb24"
            )
            frame.pts = index
            container.mux(video.encode(frame))
        container.mux(video.encode(None))
        if audio is not None:
            samples = _click_track(FRAME_COUNT_640, CLICK_FRAMES_640)
            audio_frame = av.AudioFrame.from_ndarray(
                samples[None, :], format="fltp", layout="mono"
            )
            audio_frame.sample_rate = AUDIO_RATE
            audio_frame.pts = 0
            container.mux(audio.encode(audio_frame))
            container.mux(audio.encode(None))
    return path


def _render(src: Path, out: Path, track, *, start=START, duration=DURATION) -> Path:
    render_service.render_clip(
        str(src), str(out), start, start + duration, word_timings=[], crop_track=track
    )
    return out


def _check_reel(out: Path, *, audio: bool = True) -> None:
    with av.open(str(out)) as container:
        v = container.streams.video[0]
        assert (v.codec_context.width, v.codec_context.height) == (640, 1136)
        assert v.codec_context.pix_fmt == "yuv420p"
        assert v.average_rate == FPS
        assert v.frames == round(DURATION * FPS)
        assert [a.codec_context.name for a in container.streams.audio] == (
            ["aac"] if audio else []
        )


def _assert_sync(out: Path):
    report = assert_av_sync(
        out,
        OUTPUT_BLOCK,
        fps=FPS,
        click_frames=CLICK_FRAMES_640,
        source_start=START,
        require_contiguous=True,
    )
    readings = read_frame_indices(out, OUTPUT_BLOCK)
    assert readings[0].time == 0.0
    assert readings[0].index == FIRST_FRAME
    assert report.frames_checked == round(DURATION * FPS)
    assert report.clicks_checked == 3  # source frames 37, 61, 90
    return report


def test_pinned_track_keeps_av_sync(tmp_path):
    centred = (SRC_W - BLOCK_W) // 2  # 240
    assert PINNED_TRACK[0][1] + BLOCK_IN_WINDOW == centred
    src = _write_source(tmp_path / "centred.mp4", lambda _i: centred)
    out = _render(src, tmp_path / "pinned.mp4", PINNED_TRACK)
    _check_reel(out)
    _assert_sync(out)


def test_moving_track_pans_on_time_and_keeps_av_sync(tmp_path):
    def block_left(index: int) -> int:
        t = (index - FIRST_FRAME) / FPS  # chapter time of source frame ``index``
        return round(_track_x(MOVING_TRACK, float(t))) + BLOCK_IN_WINDOW

    src = _write_source(tmp_path / "tracking.mp4", block_left)
    out = _render(src, tmp_path / "moving.mp4", MOVING_TRACK)
    _check_reel(out)
    _assert_sync(out)


def _ramp_source(path: Path, frames: int = 72) -> Path:
    """Static grey ramp: column x has value round(x * 255 / (W - 1))."""
    ramp = np.round(np.arange(SRC_W) * 255 / (SRC_W - 1)).astype(np.uint8)
    rgb = np.repeat(np.repeat(ramp[None, :, None], SRC_H, axis=0), 3, axis=2)
    with av.open(str(path), "w") as container:
        video = container.add_stream("libx264", rate=FPS)
        video.width, video.height, video.pix_fmt = SRC_W, SRC_H, "yuv420p"
        video.options = {"preset": "veryfast", "crf": "10"}
        for index in range(frames):
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.pts = index
            container.mux(video.encode(frame))
        container.mux(video.encode(None))
    return path


def _centre_column_luma(out: Path) -> tuple[float, float]:
    """Mean luma of the output's centre column on the first and last frame."""
    with av.open(str(out)) as container:
        lumas = [
            frame.to_ndarray(format="gray")[:, GEOMETRY.canvas_size[0] // 2]
            for frame in container.decode(container.streams.video[0])
        ]
    return float(lumas[0].mean()), float(lumas[-1].mean())


def _expected_luma(crop_left: float) -> float:
    """Ramp value of the source column under the output centre (PyAV's
    ``gray`` conversion expands limited-range luma back to 0..255)."""
    column = crop_left + WINDOW.width / 2
    return column * 255 / (SRC_W - 1)


def test_moving_track_pans_right_across_the_source(tmp_path):
    duration = 2.5
    track = [(0.0, 0.0), (duration, MAX_X)]
    src = _ramp_source(tmp_path / "ramp.mp4")
    out = _render(src, tmp_path / "ramp_reel.mp4", track, start=0.0, duration=duration)
    first, last = _centre_column_luma(out)
    last_t = duration - 1 / float(FPS)
    # left edge of the source at t=0, nearly the right edge at the end
    assert first == pytest.approx(_expected_luma(0.0), abs=4)
    assert last == pytest.approx(_expected_luma(_track_x(track, last_t)), abs=4)
    assert last - first > 120  # moved right, by most of the 438 px range


def test_sixty_four_keyframe_track_renders(tmp_path):
    track = [(i * DURATION / 63, (i * 97) % (MAX_X + 1)) for i in range(64)]
    src = _ramp_source(tmp_path / "ramp.mp4", frames=FRAME_COUNT_640)
    out = _render(src, tmp_path / "k64.mp4", track)
    _check_reel(out, audio=False)
    assert ffmpeg_tools.duration(out) == pytest.approx(DURATION, abs=0.05)
