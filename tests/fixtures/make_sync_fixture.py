"""Generate deterministic A/V sync fixtures with PyAV.

Two sources, both H.264 (libx264, B-frames on) at 23.976 fps:

* ``sync_640x360``  — ~6 s, AAC 48 kHz mono click track: one short loud
  burst starting exactly at each frame in ``CLICK_FRAMES_640``.
* ``sync_1280x720`` — ~3 s, no audio stream.

Every frame burns its own index into a top-left bit block; the layout is
documented in ``tests/sync_checker.py`` (which decodes it independently of the
encoder below). The rest of the picture is a moving gradient + sweeping bar so
the encoder produces genuine P/B-frame motion.

Outputs are cached by a hash of this file's source, so editing the generator
always forces a rebuild. Usage::

    .venv-mac/bin/python -m tests.fixtures.make_sync_fixture [out_dir]
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from tests.sync_checker import BlockGeometry

FPS = Fraction(24000, 1001)
AUDIO_RATE = 48_000
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "generated"

FRAME_COUNT_640 = 144  # 6.006 s
FRAME_COUNT_720 = 72  # 3.003 s
# Spread across the clip, >= 0.5 s apart, none at frame 0 (encoder priming).
CLICK_FRAMES_640: tuple[int, ...] = (12, 37, 61, 90, 118, 137)

_CLICK_SECONDS = 0.030
_CLICK_TONE_HZ = 1_000.0
_CLICK_AMPLITUDE = 0.8

_BLOCK_COLS = 8
_BLOCK_ROWS = 2
_DATA_BITS = 12
_WHITE = 255
_BLACK = 0
_SURROUND = 128


@dataclass(frozen=True)
class SyncFixture:
    """A generated source plus the ground truth needed to check renders of it."""

    path: Path
    width: int
    height: int
    fps: Fraction
    frame_count: int
    click_frames: tuple[int, ...]
    has_audio: bool
    geometry: BlockGeometry

    @property
    def duration(self) -> float:
        return float(self.frame_count / self.fps)

    def frame_time(self, index: int) -> float:
        """Presentation time (s) of source frame ``index``."""
        return float(index / self.fps)


def block_geometry(width: int) -> BlockGeometry:
    """Bit-block geometry in source pixels: cells of width/32 px, inset half a cell."""
    cell = width // 32
    margin = cell // 2
    return BlockGeometry(
        x=margin, y=margin, cell_w=cell, cell_h=cell, cols=_BLOCK_COLS, rows=_BLOCK_ROWS
    )


def _cells_for_index(index: int) -> list[bool]:
    """Cell on/off states (row-major): guard W, guard B, 12 bits MSB-first, parity, guard W."""
    if not 0 <= index < 2**_DATA_BITS:
        raise ValueError(f"frame index {index} does not fit in {_DATA_BITS} bits")
    data = [bool((index >> shift) & 1) for shift in range(_DATA_BITS - 1, -1, -1)]
    parity = sum(data) % 2 == 1
    return [True, False, *data, parity, True]


def _draw_frame(
    index: int, width: int, height: int, geometry: BlockGeometry
) -> np.ndarray:
    xs = np.arange(width, dtype=np.int32)
    ys = np.arange(height, dtype=np.int32)[:, None]
    rgb = np.empty((height, width, 3), dtype=np.uint8)
    rgb[..., 0] = ((xs + index * 4) % 256)[None, :]
    rgb[..., 1] = (ys + index * 2) % 256
    rgb[..., 2] = 96
    bar_x = (index * 9) % width
    rgb[:, bar_x : bar_x + width // 20] = 230

    cell = int(geometry.cell_w)
    x0, y0 = int(geometry.x), int(geometry.y)
    block_w, block_h = cell * geometry.cols, cell * geometry.rows
    pad = cell // 2
    rgb[
        max(0, y0 - pad) : y0 + block_h + pad, max(0, x0 - pad) : x0 + block_w + pad
    ] = _SURROUND
    for n, on in enumerate(_cells_for_index(index)):
        row, col = divmod(n, geometry.cols)
        top, left = y0 + row * cell, x0 + col * cell
        rgb[top : top + cell, left : left + cell] = _WHITE if on else _BLACK
    return rgb


def _click_track(frame_count: int, click_frames: tuple[int, ...]) -> np.ndarray:
    total = int(round(frame_count / FPS * AUDIO_RATE))
    samples = np.zeros(total, dtype=np.float32)
    burst_len = int(_CLICK_SECONDS * AUDIO_RATE)
    t = np.arange(burst_len, dtype=np.float32) / AUDIO_RATE
    # cos starts at its peak, so the onset is sharp to the sample.
    burst = (_CLICK_AMPLITUDE * np.cos(2 * np.pi * _CLICK_TONE_HZ * t)).astype(
        np.float32
    )
    fade = min(48, burst_len)
    burst[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    for k in click_frames:
        start = int(round(k / FPS * AUDIO_RATE))
        end = min(total, start + burst_len)
        samples[start:end] = burst[: end - start]
    return samples


def _encode(
    path: Path,
    *,
    width: int,
    height: int,
    frame_count: int,
    click_frames: tuple[int, ...],
    with_audio: bool,
) -> None:
    geometry = block_geometry(width)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp.mp4")
    with av.open(str(tmp), "w") as container:
        video = container.add_stream("libx264", rate=FPS)
        video.width, video.height, video.pix_fmt = width, height, "yuv420p"
        # veryfast keeps x264's default 3 B-frames (ultrafast would disable them).
        video.options = {"preset": "veryfast", "crf": "18", "bf": "3", "g": "48"}
        audio = None
        if with_audio:
            audio = container.add_stream("aac", rate=AUDIO_RATE)
            audio.layout = "mono"
            audio.bit_rate = 128_000

        for index in range(frame_count):
            frame = av.VideoFrame.from_ndarray(
                _draw_frame(index, width, height, geometry), format="rgb24"
            )
            frame.pts = index
            container.mux(video.encode(frame))
        container.mux(video.encode(None))

        if audio is not None:
            samples = _click_track(frame_count, click_frames)
            audio_frame = av.AudioFrame.from_ndarray(
                samples[None, :], format="fltp", layout="mono"
            )
            audio_frame.sample_rate = AUDIO_RATE
            audio_frame.pts = 0
            container.mux(audio.encode(audio_frame))
            container.mux(audio.encode(None))
    os.replace(tmp, path)


def _source_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:10]


def _build(
    out_dir: Path,
    stem: str,
    *,
    width: int,
    height: int,
    frame_count: int,
    click_frames: tuple[int, ...],
    with_audio: bool,
) -> SyncFixture:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}_{_source_hash()}.mp4"
    if not path.exists():
        for stale in out_dir.glob(f"{stem}_*.mp4"):
            stale.unlink()
        _encode(
            path,
            width=width,
            height=height,
            frame_count=frame_count,
            click_frames=click_frames,
            with_audio=with_audio,
        )
    return SyncFixture(
        path=path,
        width=width,
        height=height,
        fps=FPS,
        frame_count=frame_count,
        click_frames=click_frames,
        has_audio=with_audio,
        geometry=block_geometry(width),
    )


def build_sync_fixture_640(out_dir: Path = DEFAULT_OUT_DIR) -> SyncFixture:
    """640x360, ~6 s, B-frames, AAC click track at ``CLICK_FRAMES_640``."""
    return _build(
        out_dir,
        "sync_640x360",
        width=640,
        height=360,
        frame_count=FRAME_COUNT_640,
        click_frames=CLICK_FRAMES_640,
        with_audio=True,
    )


def build_sync_fixture_720(out_dir: Path = DEFAULT_OUT_DIR) -> SyncFixture:
    """1280x720, ~3 s, B-frames, no audio stream."""
    return _build(
        out_dir,
        "sync_1280x720",
        width=1280,
        height=720,
        frame_count=FRAME_COUNT_720,
        click_frames=(),
        with_audio=False,
    )


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT_DIR
    for fixture in (build_sync_fixture_640(out_dir), build_sync_fixture_720(out_dir)):
        print(
            fixture.path,
            f"{fixture.width}x{fixture.height}",
            f"frames={fixture.frame_count}",
            f"clicks={list(fixture.click_frames)}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
