"""Frame-accurate A/V sync checker for rendered reels.

Pairs with ``tests/fixtures/make_sync_fixture.py``. The fixture source burns
its own frame index into a corner *bit block* and carries an audio click at a
known list of frame indices. After a render (trim, scale, pad, re-encode) this
module recovers:

* which **source frame** every output frame shows (``read_frame_indices``);
* when every audio **click** starts (``click_onsets``);

and checks both against the schedule the source implies (``assert_av_sync``).
``assert_caption_band`` additionally checks that every output frame's caption
band shows exactly the caption a schedule says is visible at that time.

Bit-block format (read side — the generator encodes it independently)
-----------------------------------------------------------------------
``cols x rows`` square cells in row-major order (default 8 x 2 = 16 cells)::

    cell 0        white guard   (sets the "1" luma level)
    cell 1        black guard   (sets the "0" luma level)
    cells 2..13   12 data bits, most significant bit first
    cell 14       even parity   (XOR of the 12 data bits)
    cell 15       white guard

A cell is read as the mean luma of its central half (25 %..75 % on each axis),
thresholded halfway between the two guard levels. That survives scaling,
4:2:0 chroma subsampling and lossy re-encoding as long as a scaled cell stays
roughly >= 6 px.

Locating the block in a rendered frame
--------------------------------------
``SyncFixture.geometry`` is the block's position in **source** pixels. A
reframed reel places the source as a scaled inset on a taller canvas. Map the
geometry with ``inset_geometry``:

* default (no ``inset_rect``): the inset spans the full canvas width and is
  vertically centred, ``y0 = (canvas_h - inset_h) // 2`` — the legacy MoviePy
  placement. The ffmpeg renderer rounds ``y0`` down to even
  (``render_service.inset_y``); that 1 px difference is harmless because only
  the centre of each cell is sampled, but tests pass the exact rect anyway.
* otherwise pass ``inset_rect=(x0, y0, inset_w, inset_h)`` measured from the
  render's filtergraph.

If a render's placement is unknown, dump one frame (``ffmpeg -frames:v 1``),
look for the black/white chequer in the inset's top-left corner and pass its
rectangle explicitly.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

DATA_BITS = 12
_GUARD_WHITE_CELLS = (0, 15)
_GUARD_BLACK_CELL = 1
_FIRST_DATA_CELL = 2
_PARITY_CELL = _FIRST_DATA_CELL + DATA_BITS  # 14
_MIN_GUARD_CONTRAST = 60.0

# Click detection.
_CLICK_THRESHOLD_RATIO = 0.5
_CLICK_MIN_GAP_S = 0.25


@dataclass(frozen=True)
class BlockGeometry:
    """Position of the bit block in a frame, in (possibly fractional) pixels."""

    x: float
    y: float
    cell_w: float
    cell_h: float
    cols: int = 8
    rows: int = 2

    def transformed(
        self, scale_x: float, scale_y: float, dx: float, dy: float
    ) -> BlockGeometry:
        """Return the geometry after scaling by (scale_x, scale_y) then shifting by (dx, dy)."""
        return BlockGeometry(
            x=self.x * scale_x + dx,
            y=self.y * scale_y + dy,
            cell_w=self.cell_w * scale_x,
            cell_h=self.cell_h * scale_y,
            cols=self.cols,
            rows=self.rows,
        )


def inset_geometry(
    source: BlockGeometry,
    *,
    source_size: tuple[int, int],
    canvas_size: tuple[int, int],
    inset_rect: tuple[float, float, float, float] | None = None,
) -> BlockGeometry:
    """Map a source-frame block geometry into a reframed canvas.

    With ``inset_rect=None`` the inset is assumed to span the canvas width and
    be vertically centred (see module docstring).
    """
    src_w, src_h = source_size
    canvas_w, canvas_h = canvas_size
    if inset_rect is None:
        inset_w = canvas_w
        inset_h = round(src_h * canvas_w / src_w)
        inset_rect = (0, (canvas_h - inset_h) // 2, inset_w, inset_h)
    x0, y0, inset_w, inset_h = inset_rect
    return source.transformed(inset_w / src_w, inset_h / src_h, x0, y0)


def _cell_means(luma: np.ndarray, geometry: BlockGeometry) -> list[float] | None:
    height, width = luma.shape
    means: list[float] = []
    for cell in range(geometry.cols * geometry.rows):
        row, col = divmod(cell, geometry.cols)
        left = geometry.x + col * geometry.cell_w
        top = geometry.y + row * geometry.cell_h
        x0 = math.floor(left + geometry.cell_w * 0.25)
        x1 = math.ceil(left + geometry.cell_w * 0.75)
        y0 = math.floor(top + geometry.cell_h * 0.25)
        y1 = math.ceil(top + geometry.cell_h * 0.75)
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height or x1 <= x0 or y1 <= y0:
            return None
        means.append(float(luma[y0:y1, x0:x1].mean()))
    return means


def decode_index(luma: np.ndarray, geometry: BlockGeometry) -> int | None:
    """Decode the frame index from a 2-D luma array, or ``None`` if no valid block."""
    means = _cell_means(luma, geometry)
    if means is None:
        return None
    white = min(means[c] for c in _GUARD_WHITE_CELLS)
    black = means[_GUARD_BLACK_CELL]
    if white - black < _MIN_GUARD_CONTRAST:
        return None
    threshold = (white + black) / 2
    bits = [m > threshold for m in means]
    data = bits[_FIRST_DATA_CELL:_PARITY_CELL]
    parity = sum(data) % 2 == 1
    if parity != bits[_PARITY_CELL]:
        return None
    value = 0
    for bit in data:
        value = (value << 1) | int(bit)
    return value


@dataclass(frozen=True)
class FrameReading:
    """One decoded output frame: presentation time and the source index it shows."""

    time: float
    index: int | None


def read_frame_indices(path: str | Path, geometry: BlockGeometry) -> list[FrameReading]:
    """Decode every video frame of ``path`` and read its embedded source index."""
    readings: list[FrameReading] = []
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        for frame in container.decode(stream):
            luma = frame.to_ndarray(format="gray")
            readings.append(
                FrameReading(time=float(frame.time), index=decode_index(luma, geometry))
            )
    return readings


@dataclass(frozen=True)
class _Audio:
    samples: np.ndarray  # mono float32
    rate: int
    start: float  # presentation time (s) of the first sample

    @property
    def end(self) -> float:
        return self.start + self.samples.size / self.rate


def _decode_audio(path: str | Path) -> _Audio | None:
    """Decode the first audio stream to mono float32, or ``None`` if there is none."""
    with av.open(str(path)) as container:
        if not container.streams.audio:
            return None
        stream = container.streams.audio[0]
        rate = stream.codec_context.sample_rate
        resampler = av.AudioResampler(format="flt", layout="mono", rate=rate)
        chunks: list[np.ndarray] = []
        start: float | None = None
        for frame in container.decode(stream):
            if start is None:
                start = float(frame.time) if frame.time is not None else 0.0
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return None
    return _Audio(np.concatenate(chunks), rate, start or 0.0)


def _onsets(audio: _Audio, threshold_ratio: float, min_gap_s: float) -> list[float]:
    magnitude = np.abs(audio.samples)
    peak = float(magnitude.max()) if magnitude.size else 0.0
    if peak <= 0.0:
        return []
    loud = np.flatnonzero(magnitude >= peak * threshold_ratio)
    min_gap = int(min_gap_s * audio.rate)
    onsets: list[float] = []
    last = -min_gap - 1
    for sample in loud:
        if sample - last > min_gap:
            onsets.append(audio.start + int(sample) / audio.rate)
        last = sample
    return onsets


def click_onsets(
    path: str | Path,
    *,
    threshold_ratio: float = _CLICK_THRESHOLD_RATIO,
    min_gap_s: float = _CLICK_MIN_GAP_S,
) -> list[float]:
    """Return the start time (s) of each audio click; ``[]`` when there is no audio.

    An onset is the first sample whose magnitude reaches ``threshold_ratio`` of
    the track's peak after at least ``min_gap_s`` of sub-threshold signal.
    """
    audio = _decode_audio(path)
    return [] if audio is None else _onsets(audio, threshold_ratio, min_gap_s)


def frame_error(
    index: int, time: float, fps: Fraction, *, source_start: float = 0.0
) -> float:
    """Signed offset, in source frames, of the shown frame from the ideal source time.

    ``index - (time + source_start) * fps``. Both legitimate renderers land in
    ``(-1, +1)``, using different rounding conventions (measured):

    * MoviePy shows the frame whose display interval *contains* the source
      time (floor) → error in ``(-1, 0]``; e.g. a chapter at 0.5 s opens on
      frame 11 (pts 0.459 s): error 11 - 0.5·23.976 = -0.99.
    * ffmpeg ``-ss S`` re-encodes snap the first kept frame onto the output
      grid (nearest) → error in ``[-0.5, +0.5]``; ``-ss 1.0`` shows frame 24
      (pts 1.001 s) at t=0.

    A whole-frame slip (dropped/duplicated frame, wrong start offset) pushes
    ``|error|`` to >= 1, which is what ``assert_av_sync`` rejects.
    """
    return index - (time + source_start) * float(fps)


@dataclass(frozen=True)
class SyncReport:
    """Summary of a passing ``assert_av_sync`` run."""

    frames_checked: int
    clicks_checked: int
    max_abs_frame_error: float
    max_click_error_s: float
    click_errors_s: tuple[float, ...] = field(default=())


# Clicks starting this close to the end of the audio are not required.
_CLICK_END_MARGIN_S = 0.005


def assert_av_sync(
    path: str | Path,
    geometry: BlockGeometry,
    *,
    fps: Fraction,
    click_frames: Sequence[int],
    source_start: float = 0.0,
    max_frame_error: float = 1.0,
    audio_tolerance_s: float | None = None,
    require_contiguous: bool = False,
) -> SyncReport:
    """Assert a render of the sync fixture shows and sounds each source frame on time.

    Checks, against the schedule implied by ``source_start``:

    * every decoded frame carries a readable source index whose
      ``frame_error`` is strictly within ``±max_frame_error`` frames;
    * the clicks whose source time falls inside the rendered audio are all
      present, in order, each within ``audio_tolerance_s`` of
      ``k / fps - source_start``.

    Args:
        path: rendered mp4.
        geometry: bit-block location in the *rendered* frame (see ``inset_geometry``).
        fps: source frame rate (``SyncFixture.fps``).
        click_frames: source frame indices carrying a click (``()`` for no audio).
        source_start: source time (s) that output t=0 corresponds to (chapter start).
        max_frame_error: exclusive bound on ``|frame_error|``, in frames.
        audio_tolerance_s: allowed |onset - expected| per click; defaults to one
            AAC frame (1024 samples) at the render's audio sample rate.
        require_contiguous: also require consecutive frames to show consecutive
            source indices (no duplicated or skipped frame). Only meaningful when
            the render keeps the source's constant frame rate. A one-frame slip
            after frame 0 can stay within ``±max_frame_error`` (e.g. +0.01 on
            frame 0, -0.99 afterwards); this catches it.

    Raises:
        AssertionError: describing every out-of-tolerance frame or click.
    """
    readings = read_frame_indices(path, geometry)
    assert readings, f"{path}: no video frames decoded"

    problems: list[str] = []
    worst = 0.0
    for n, reading in enumerate(readings):
        if reading.index is None:
            problems.append(f"frame {n} @ {reading.time:.4f}s: frame index unreadable")
            continue
        error = frame_error(reading.index, reading.time, fps, source_start=source_start)
        worst = max(worst, abs(error))
        if abs(error) >= max_frame_error:
            ideal = (reading.time + source_start) * float(fps)
            problems.append(
                f"frame {n} @ {reading.time:.4f}s: frame index {reading.index}, "
                f"source position {ideal:.2f} (off by {error:+.2f} frames)"
            )

    if require_contiguous:
        indices = [r.index for r in readings]
        for n, (prev, cur) in enumerate(zip(indices, indices[1:]), start=1):
            if prev is not None and cur is not None and cur - prev != 1:
                problems.append(
                    f"frame {n} @ {readings[n].time:.4f}s: frame index {cur} follows "
                    f"{prev} (duplicated or skipped source frame)"
                )

    audio = _decode_audio(path)
    expected_clicks: list[float] = []
    click_errors: list[float] = []
    if audio is not None:
        expected_clicks = [
            float(k / fps) - source_start
            for k in click_frames
            if 0.0 <= float(k / fps) - source_start < audio.end - _CLICK_END_MARGIN_S
        ]
        tolerance = (
            1024 / audio.rate if audio_tolerance_s is None else audio_tolerance_s
        )
        onsets = _onsets(audio, _CLICK_THRESHOLD_RATIO, _CLICK_MIN_GAP_S)
        if len(onsets) != len(expected_clicks):
            problems.append(
                f"click count {len(onsets)} != expected {len(expected_clicks)}: "
                f"onsets={[round(t, 4) for t in onsets]} "
                f"expected={[round(t, 4) for t in expected_clicks]}"
            )
        else:
            for got, want in zip(onsets, expected_clicks):
                error_s = got - want
                click_errors.append(error_s)
                if abs(error_s) > tolerance:
                    problems.append(
                        f"click @ {got:.4f}s, expected {want:.4f}s "
                        f"(off by {error_s * 1000:+.1f} ms, tolerance {tolerance * 1000:.1f} ms)"
                    )
    elif click_frames:
        problems.append(f"no audio stream, but {len(click_frames)} click(s) expected")

    if problems:
        shown = "\n  ".join(problems[:20])
        more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
        raise AssertionError(f"A/V sync check failed for {path}:\n  {shown}{more}")

    return SyncReport(
        frames_checked=len(readings),
        clicks_checked=len(expected_clicks),
        max_abs_frame_error=worst,
        max_click_error_s=max((abs(e) for e in click_errors), default=0.0),
        click_errors_s=tuple(click_errors),
    )


# ── Caption band ──────────────────────────────────────────────────────────────

# BT.601 limited-range luma, the conversion ffmpeg applies to RGB inputs that
# carry no colour metadata (the background PNG and the caption PNGs).
_LUMA_COEFFS = np.array([65.481, 128.553, 24.966]) / 255.0


def rgb_to_luma(rgb: np.ndarray) -> np.ndarray:
    """``uint8`` RGB (H, W, 3) → float BT.601 limited-range luma (16..235)."""
    return 16.0 + rgb[..., :3].astype(np.float64) @ _LUMA_COEFFS


@dataclass(frozen=True)
class CaptionBand:
    """What the caption band of a reel must look like, frame by frame.

    ``rect`` is ``(x0, y0, x1, y1)`` in output-frame pixels; ``background`` is
    the RGB background under it; ``images`` maps a caption key to its RGBA
    image of the rect's size; ``schedule`` lists ``(key, start, duration)``
    in output seconds — where entries overlap the one listed later wins, and
    no entry (or a non-positive duration) means the band shows the background.
    """

    rect: tuple[int, int, int, int]
    background: np.ndarray
    images: dict[object, np.ndarray]
    schedule: Sequence[tuple[object, float, float]]

    def expected_key(self, t: float) -> object | None:
        key = None
        for k, start, duration in self.schedule:
            if duration > 0 and start <= t < start + duration:
                key = k
        return key

    def candidates(self) -> dict[object | None, np.ndarray]:
        """Expected band luma per caption (``None`` = no caption)."""
        bg = self.background[..., :3].astype(np.float64)
        out: dict[object | None, np.ndarray] = {None: rgb_to_luma(bg)}
        for key, rgba in self.images.items():
            alpha = rgba[..., 3:4].astype(np.float64) / 255.0
            comp = rgba[..., :3].astype(np.float64) * alpha + bg * (1.0 - alpha)
            out[key] = rgb_to_luma(comp)
        return out


@dataclass(frozen=True)
class CaptionBandReport:
    frames_checked: int
    captioned_frames: int
    max_abs_luma_error: float
    worst_mean_error: float
    best_wrong_mean_error: float


def _luma_plane(frame) -> np.ndarray:
    if frame.format.name not in ("yuv420p", "yuvj420p"):
        raise AssertionError(f"expected yuv420p output, got {frame.format.name}")
    return frame.to_ndarray()[: frame.height]


def assert_caption_band(
    path: str | Path,
    band: CaptionBand,
    *,
    tolerance: float | None = 2.0,
    max_mean_error: float = 8.0,
) -> CaptionBandReport:
    """Assert every frame's caption band shows the scheduled caption.

    Compares the decoded **luma** of ``band.rect`` with the expected
    composite (caption alpha-blended over ``band.background``):

    * ``tolerance`` set (lossless renders): every pixel within ``±tolerance``
      of the scheduled caption's composite — ffmpeg blends in YUV and rounds,
      the oracle blends in float, so ±2 covers blend rounding;
    * ``tolerance=None`` (lossy renders): the scheduled composite must be the
      nearest candidate by mean absolute error and within ``max_mean_error``.

    Chroma is not compared: 4:2:0 subsampling smears coloured glyph edges
    across 2x2 blocks, so per-channel RGB equality cannot hold at text edges.
    """
    x0, y0, x1, y1 = band.rect
    candidates = band.candidates()
    problems: list[str] = []
    frames = captioned = 0
    worst_px = worst_mean = 0.0
    best_wrong = math.inf
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        for n, frame in enumerate(container.decode(stream)):
            frames += 1
            t = float(frame.time)
            luma = _luma_plane(frame)
            if y1 > luma.shape[0] or x1 > luma.shape[1]:
                raise AssertionError(
                    f"caption rect {band.rect} exceeds frame {luma.shape[::-1]}"
                )
            got = luma[y0:y1, x0:x1].astype(np.float64)
            want_key = band.expected_key(t)
            captioned += want_key is not None
            errors = {k: float(np.abs(got - v).mean()) for k, v in candidates.items()}
            worst_mean = max(worst_mean, errors[want_key])
            wrong = [e for k, e in errors.items() if k != want_key]
            if wrong:
                best_wrong = min(best_wrong, min(wrong))
            if tolerance is not None:
                px = float(np.abs(got - candidates[want_key]).max())
                worst_px = max(worst_px, px)
                if px > tolerance:
                    nearest = min(errors, key=errors.get)
                    problems.append(
                        f"frame {n} @ {t:.4f}s: band differs from {want_key!r} by "
                        f"up to {px:.1f} luma (tolerance {tolerance}); nearest "
                        f"candidate is {nearest!r}"
                    )
            else:
                nearest = min(errors, key=errors.get)
                if nearest != want_key or errors[want_key] > max_mean_error:
                    problems.append(
                        f"frame {n} @ {t:.4f}s: expected {want_key!r} "
                        f"(mean error {errors[want_key]:.2f}), nearest is "
                        f"{nearest!r} ({errors[nearest]:.2f})"
                    )
    assert frames, f"{path}: no video frames decoded"
    if problems:
        shown = "\n  ".join(problems[:20])
        more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
        raise AssertionError(f"caption band check failed for {path}:\n  {shown}{more}")
    return CaptionBandReport(
        frames_checked=frames,
        captioned_frames=captioned,
        max_abs_luma_error=worst_px,
        worst_mean_error=worst_mean,
        best_wrong_mean_error=best_wrong,
    )
