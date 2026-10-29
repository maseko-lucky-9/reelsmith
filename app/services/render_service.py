"""One-pass ffmpeg reel renderer.

``render_clip`` trims the chapter straight out of the SOURCE and composites
the reel in a single ffmpeg process — no intermediate chapter mp4, no
per-frame Python:

    -ss S -t D -i SRC   -i bg.png   -f concat -safe 0 -i captions.ffconcat

    [1:v] loop=-1:1:0, settb=1/L, setpts=N*STEP          [bg]   decoded once
    [0:v] setpts=PTS-STARTPTS, scale=W:-2                [in]   frame 0 = inset
    [bg][in] overlay=0:EVEN_Y:ts_sync_mode=nearest        [b]
    [b][2:v] overlay=CX:CY:eof_action=pass                [c]   CX, CY even
    [c] crop=even w/h, format=yuv420p                    [v]

    -map [v] -map 0:a:0? -t D -r FPS  libx264 ultrafast crf 28, aac

Timing rules (each verified against the sync fixtures, see
tests/unit/test_render_sync.py):

* ``setpts=PTS-STARTPTS`` puts the first kept source frame at t=0, so frame 0
  always carries the inset.
* The background's frames are stamped at exactly k/FPS in a time base
  ``1/L`` with ``L = lcm(FPS numerator, 1000)``: both the output frame grid
  and the captions' millisecond start times are exact in it. A coarser base
  (the PNG demuxer's 1/25 s, or 1/FPS) rounds caption boundaries and frame
  times onto each other and shows the wrong word / a duplicated frame.
* The inset overlay syncs to the NEAREST source frame: mkv/webm store 1 ms
  timestamps, so frame k sits up to 0.5 ms either side of k/FPS and the
  default "last frame <= t" rule jitters.
* ``-t D`` is explicit: the looped background is infinite.
* VFR sources are written at a constant ``ffmpeg_tools.fps`` (average) rate.

Moving crop (``crop_track``, reframe): instead of the letterboxed inset, a
canvas-aspect window of the source pans horizontally and fills the canvas::

    [0:v] setpts=PTS-STARTPTS, crop=w=CW:h=CH:x='X(t)':y=CY,
          scale=W:H                                      [in]   full bleed
    [bg][in] overlay=0:0:ts_sync_mode=nearest             [b]

Everything else (background grid, nearest sync, captions, even crop,
yuv420p, ``-map 0:a:0?``) is the same graph. ``X(t)`` is ``crop_x_expr``: a
FLAT sum of half-open per-segment terms (ffmpeg's expression parser limits
nesting depth, so a chain of nested ``if()`` cannot hold 64 keyframes). crop
evaluates ``x`` for every frame, with ``t`` = seconds since the first kept
frame (the chapter start), and rounds it down to an even column for 4:2:0.

B-roll (``broll``): each insert is one more input, looped and cut to its
window, shifted onto the clip's frame grid, cover-fitted to the canvas and
overlaid on the composite BEFORE the captions, so captions stay on top::

    -stream_loop -1 -t LEN -i insert                       (after captions)
    [k:v] setpts=PTS-STARTPTS+T0/TB, fps=FPS,
          scale=W:H:force_original_aspect_ratio=increase, crop=W:H,
          setsar=1, format=yuv420p                       [brI]
    [b][br0] overlay=0:0:enable='gte(t,LO)*lt(t,HI)':eof_action=pass  [b0]
    [b0][br1] overlay=...                                 [b1]
    [b1][2:v] overlay=CX:CY:eof_action=pass               [c]   captions

* The pts trap: an input starts at ITS OWN pts 0, so without the shift a
  window starting after the insert's length finds it already at EOF (nothing
  shown with ``eof_action=pass``, a frozen last frame with the default
  ``repeatlast``). ``T0`` is the time of the first output frame the window
  covers, so the insert's frame 0 lands exactly there; ``fps`` puts every
  insert frame on the clip's grid, so overlay's default "last frame <= t"
  sync is exact (no ``ts_sync_mode=nearest`` needed).
* Window: output frame k shows the insert iff start <= k/FPS < start +
  duration, decided in exact rationals. The gate's bounds ``LO``/``HI`` sit
  half a frame before the first covered and the first uncovered frame, so
  a floating-point ``t`` can never flip an edge frame. ``LEN`` is exactly
  the covered span (the fps filter's EOF rounding fills its last frame), so
  a short insert loops over the whole window, and ``eof_action=pass``
  stops anything lingering after it.
* Grid and audio: overlay emits one frame per MAIN frame with the main
  frame's timestamp (the inserts never drive output frames), so the settb
  grid, frame count and duration are those of the render without B-roll.
  Insert audio is never mapped (only ``-map 0:a:0?``).

Edited timeline (``plan``, spec 016): the editor's render plan in the SAME
single pass. The source is read once over the span of its segments, each
segment is cut out with trim/atrim and the pieces are concatenated::

    -ss S0 -t SPAN -i SRC   -i bg.png   [-f concat ... captions]
    [-i overlay_N.png ...]  [-i voiceover_N ...]

    [0:v] split=N [sv0][sv1]...       (N > 1)
    [sv0] trim=start=A:end=B, setpts=PTS-STARTPTS, INSET0   [g0]
    [0:a] asplit=N ... atrim=start=A:end=B, asetpts=...     [h0]
    [g0][h0][g1][h1] concat=n=N:v=1:a=1                     [in][sa]
    [bg][in] overlay ... [b] -> captions [c] -> overlays [oK] -> even crop [v]

    [k:a] atrim=end=LEN, asetpts, volume=GAIN, adelay=MS:all=1  [voK]
    [sa] apad=whole_dur=T, volume='if(between(t,..)+..,DUCK,1)' [base]
    [base][vo0]... amix=inputs=..:duration=first:normalize=0    [a]

* Segments must be in source order and must not overlap: the source is
  read forward once, so a branch never buffers frames for a later segment.
* The concat output is the inset; the background grid, nearest sync, even
  crop and encoder are the single-window graph's. ``-t`` is the sum of the
  segment lengths.
* Captions are the plan's (per-word timings), drawn as PNGs by
  ``create_subtitle_image`` in the ``caption_style`` preset's colours and
  size: never a second ``ass=`` pass. Text overlays are PIL-drawn PNG inputs
  gated with ``enable='between(t,S,E)'``; no client text enters the graph.
* Voice-overs start at their reel time (``adelay``), the source is ducked to
  ``VOICEOVER_DUCK_VOLUME`` while one plays and dropped when ``mute_source``
  (a silent base then keeps the audio track). A source without audio and no
  voice-over gives a reel without audio, as the single-window path does.
* The crop track's t=0 is ``render_clip``'s ``start``; each segment's pan
  expression is that track re-based to the segment (``segment_crop_track``).
"""

import logging
import math
import os
from bisect import bisect_left, bisect_right
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING

import pysrt
from PIL import Image
from webvtt import WebVTT

import app.logging_config  # noqa: F401
from app.services import caption_track, clip_service, ffmpeg_tools
from app.services.subtitle_image_service import (
    CaptionStyle,
    caption_style_for_preset,
    create_text_overlay_image,
)
from app.settings import settings

if TYPE_CHECKING:  # pragma: no cover - import only for typing
    from app.services.timeline_render_service import (
        CaptionItem,
        RenderPlan,
        TextOverlayItem,
    )

log = logging.getLogger(__name__)

_VIDEO_CODEC_ARGS: tuple[str, ...] = (
    "-c:v",
    "libx264",
    "-preset",
    "ultrafast",  # much faster encode; fine for review clips
    "-crf",
    "28",  # slightly lower quality, faster
)
_AUDIO_CODEC_ARGS: tuple[str, ...] = ("-c:a", "aac")
# Even width/height for 4:2:0 (the 9:16 canvas of an even-width source is
# usually odd: 640 → 1137).
_EVEN_CROP = r"crop=iw-mod(iw\,2):ih-mod(ih\,2):0:0,format=yuv420p"


def _load_captions(captions_path: str):
    suffix = Path(captions_path).suffix.lower()
    if suffix == ".srt":
        return pysrt.open(captions_path)
    if suffix == ".vtt":
        return list(WebVTT().read(captions_path))
    raise ValueError(f"Unsupported captions extension: {suffix}")


def _threads() -> int:
    return min(os.cpu_count() or 2, 8)


def _rate(fps: Fraction) -> str:
    return f"{fps.numerator}/{fps.denominator}"


_INT32_MAX = 2**31 - 1


def grid_rate(fps: Fraction) -> Fraction:
    """Output frame rate whose frame grid fits ffmpeg's 32-bit time base.

    The background is stamped in a ``1/lcm(numerator, 1000)`` time base so frame
    times and millisecond caption starts are both exact. Common rates
    (24000/1001, 30000/1001, 60000/1001, 25, 30, ...) keep that exactly. VFR
    averages can have huge numerators (576089600/19266773) that overflow it;
    those are snapped to the nearest rate with denominator <= 1001, which
    bounds the lcm at ~1000x the frame rate.
    """
    if math.lcm(fps.numerator, 1000) <= _INT32_MAX:
        return fps
    return fps.limit_denominator(1001)


def even_floor(value: int) -> int:
    return value - value % 2


def inset_y(geometry: clip_service.ReelGeometry) -> int:
    """Top of the vertically centred inset, rounded down to an even row."""
    return even_floor((geometry.canvas_size[1] - geometry.inset_height) // 2)


# ── Moving crop (reframe) ─────────────────────────────────────────────────────

# A keyframe is (t, x_left): ``t`` seconds from the chapter start (output
# t=0), ``x_left`` the crop window's left edge in SOURCE pixels. Values outside
# [0, src_w - crop_w] are clamped before interpolation, so every interpolated
# x is in range too.
CropKeyframe = tuple[float, float]
CropTrack = Sequence[CropKeyframe]

MAX_CROP_KEYFRAMES = 64
_TIME_DECIMALS = 6
_X_DECIMALS = 3


@dataclass(frozen=True)
class PanCrop:
    """Size and fixed top row of the moving crop window, in source pixels."""

    width: int
    height: int
    y: int


def pan_crop(geometry: clip_service.ReelGeometry) -> PanCrop:
    """The largest even, canvas-aspect window of the source.

    A landscape source keeps its full height and pans horizontally over
    ``src_w - width`` columns; a source already narrower than the canvas
    aspect keeps its full width (no pan room) and is cropped vertically
    around its centre instead.
    """
    src_w, src_h = geometry.source_size
    canvas_w, canvas_h = geometry.canvas_size
    width = even_floor(min(src_w, round(src_h * canvas_w / canvas_h)))
    height = even_floor(min(src_h, round(src_w * canvas_h / canvas_w)))
    if width <= 0 or height <= 0:
        raise ValueError(f"source {src_w}x{src_h} is too small for a pan crop")
    return PanCrop(width=width, height=height, y=even_floor((src_h - height) // 2))


def _num(value: float, decimals: int) -> str:
    """Shortest fixed-point text for ``value`` (no exponent: ffmpeg's parser
    would read ``1e-07`` fine, but fixed text keeps knots exact and readable)."""
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _normalise_track(track: CropTrack, max_x: float) -> list[CropKeyframe]:
    """Validate, round and clamp keyframes exactly as they are printed."""
    if not 1 <= len(track) <= MAX_CROP_KEYFRAMES:
        raise ValueError(
            f"crop track needs 1..{MAX_CROP_KEYFRAMES} keyframes, got {len(track)}"
        )
    knots: list[CropKeyframe] = []
    for t, x in track:
        if not (math.isfinite(t) and math.isfinite(x)):
            raise ValueError(f"crop keyframe ({t}, {x}) is not finite")
        if t < 0:
            raise ValueError(f"crop keyframe time {t} is negative")
        t = round(t, _TIME_DECIMALS)
        if knots and t < knots[-1][0]:
            raise ValueError(
                f"crop keyframe times must not decrease ({t} after {knots[-1][0]})"
            )
        knots.append((t, round(min(max(x, 0.0), max_x), _X_DECIMALS)))
    return knots


def crop_x_expr(track: CropTrack, src_w: int, crop_w: int) -> str:
    """ffmpeg expression for the crop's left edge at time ``t``.

    Piecewise-linear through the keyframes, holding the first value before
    the first keyframe and the last value from the last one on. Built as a
    flat sum so its depth does not grow with the keyframe count::

        lt(t,t0)*x0 + sum_i gte(t,ti)*lt(t,ti+1)*(xi + dxi*(t-ti)/dti)
                    + gte(t,tn)*xn

    The intervals are half-open and share their printed endpoints, so
    exactly one term is non-zero for every ``t``, knots included (``between``
    is inclusive at both ends and would count a knot twice). Keyframes at the
    same time make a hard cut: the zero-length segment between them emits no
    term, the earlier value ends the incoming ramp and the later one starts
    the next. The expression contains no ``'``, ``:``, ``;``, ``[`` or ``]``,
    so it can be single-quoted inside a filtergraph.
    """
    if not 0 < crop_w <= src_w:
        raise ValueError(f"crop width {crop_w} must be in (0, {src_w}]")
    knots = _normalise_track(track, float(src_w - crop_w))
    if len({x for _t, x in knots}) == 1:
        return _num(knots[0][1], _X_DECIMALS)

    def t_(v: float) -> str:
        return _num(v, _TIME_DECIMALS)

    def x_(v: float) -> str:
        return _num(v, _X_DECIMALS)

    (t0, x0), (tn, xn) = knots[0], knots[-1]
    terms = [f"lt(t,{t_(t0)})*{x_(x0)}"]
    for (ta, xa), (tb, xb) in zip(knots, knots[1:]):
        if tb == ta:
            continue  # zero-length segment: a hard cut, never active
        dx = round(xb - xa, _X_DECIMALS)
        sign = "+" if dx >= 0 else "-"
        terms.append(
            f"gte(t,{t_(ta)})*lt(t,{t_(tb)})"
            f"*({x_(xa)}{sign}{x_(abs(dx))}*(t-{t_(ta)})/{t_(tb - ta)})"
        )
    terms.append(f"gte(t,{t_(tn)})*{x_(xn)}")
    return "+".join(terms)


def _pan_chain(geometry: clip_service.ReelGeometry, track: CropTrack) -> str:
    """``[in]`` filters for the moving crop: pan the window, fill the canvas."""
    src_w, _src_h = geometry.source_size
    canvas_w, canvas_h = geometry.canvas_size
    window = pan_crop(geometry)
    x = crop_x_expr(track, src_w, window.width)
    return (
        f"crop=w={window.width}:h={window.height}:x='{x}':y={window.y},"
        f"scale={canvas_w}:{canvas_h}"
    )


# ── B-roll inserts ────────────────────────────────────────────────────────────

MAX_BROLL_INSERTS = 4


@dataclass(frozen=True)
class BrollInsert:
    """A clip shown full-canvas over ``[start, start + duration)``.

    Times are seconds from the clip start (output t=0) and are compared at
    microsecond precision. The insert plays from its own first frame, loops
    if it is shorter than ``duration`` and is cover-fitted to the canvas; its
    audio is never used.
    """

    path: str
    start: float
    duration: float


def _exact(seconds: float) -> Fraction:
    """``seconds`` rounded to microseconds, as an exact rational."""
    return Fraction(f"{seconds:.{_TIME_DECIMALS}f}")


def _normalise_broll(
    broll: Sequence[BrollInsert] | None, clip_duration: float
) -> tuple[BrollInsert, ...]:
    """Timing rules only (no file access): rounded inserts sorted by start."""
    if not broll:
        return ()
    if len(broll) > MAX_BROLL_INSERTS:
        raise ValueError(
            f"at most {MAX_BROLL_INSERTS} B-roll inserts per clip, got {len(broll)}"
        )
    inserts = []
    for insert in broll:
        if not (math.isfinite(insert.start) and math.isfinite(insert.duration)):
            raise ValueError(f"B-roll insert {insert.path}: times are not finite")
        start = round(insert.start, _TIME_DECIMALS)
        duration = round(insert.duration, _TIME_DECIMALS)
        if duration <= 0:
            raise ValueError(
                f"B-roll insert {insert.path}: duration must be > 0, got {duration}"
            )
        if start < 0:
            raise ValueError(
                f"B-roll insert {insert.path}: start must be >= 0, got {start}"
            )
        if _exact(start) + _exact(duration) > _exact(clip_duration):
            raise ValueError(
                f"B-roll insert {insert.path}: ends at {start + duration:g} s, "
                f"past the clip end ({clip_duration:g} s)"
            )
        inserts.append(BrollInsert(insert.path, start, duration))
    inserts.sort(key=lambda i: (i.start, i.duration))
    for a, b in zip(inserts, inserts[1:]):
        if _exact(a.start) + _exact(a.duration) > _exact(b.start):
            raise ValueError(
                f"B-roll inserts overlap: {a.path} [{a.start:g}, "
                f"{a.start + a.duration:g}) and {b.path} from {b.start:g}"
            )
    return tuple(inserts)


def validate_broll(
    broll: Sequence[BrollInsert] | None, clip_duration: float
) -> tuple[BrollInsert, ...]:
    """Check B-roll inserts against a clip of ``clip_duration`` seconds.

    Every file must exist; each window needs ``duration > 0``, ``start >= 0``
    and ``start + duration <= clip_duration``; windows may touch but not
    overlap; at most ``MAX_BROLL_INSERTS``. Returns the inserts sorted by
    start (``()`` for ``None`` or empty). Raises ``ValueError``.
    """
    inserts = _normalise_broll(broll, clip_duration)
    for insert in inserts:
        if not Path(insert.path).is_file():
            raise ValueError(f"B-roll insert {insert.path} does not exist")
    return inserts


def _broll_frames(insert: BrollInsert, fps: Fraction) -> tuple[int, int]:
    """Output frames ``[first, end)`` the insert covers: every k with
    ``start <= k / fps < start + duration``, in exact arithmetic."""
    t0 = _exact(insert.start)
    return math.ceil(t0 * fps), math.ceil((t0 + _exact(insert.duration)) * fps)


def _broll_input(insert: BrollInsert, fps: Fraction) -> list[str]:
    """Looped input cut to exactly the span of output frames it covers."""
    first, end = _broll_frames(insert, fps)
    length = float((end - first) / fps)
    return ["-stream_loop", "-1", "-t", f"{length:.6f}", "-i", str(insert.path)]


def _broll_filters(
    insert: BrollInsert,
    index: int,
    n: int,
    under: str,
    fps: Fraction,
    geometry: clip_service.ReelGeometry,
) -> list[str]:
    """Insert chain ``[brN]`` and its overlay on ``[under]`` into ``[bN]``."""
    canvas_w, canvas_h = geometry.canvas_size
    first, end = _broll_frames(insert, fps)
    offset = Fraction(first) / fps
    # gate bounds half a frame before the first covered / uncovered frame
    lo = max(Fraction(0), (first - Fraction(1, 2)) / fps)
    hi = (end - Fraction(1, 2)) / fps

    def t_(v: Fraction) -> str:
        return _num(float(v), _TIME_DECIMALS)

    return [
        f"[{index}:v]setpts=PTS-STARTPTS+{t_(offset)}/TB,fps={_rate(fps)},"
        f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,"
        f"crop={canvas_w}:{canvas_h},setsar=1,format=yuv420p[br{n}]",
        f"[{under}][br{n}]overlay=x=0:y=0:"
        f"enable='gte(t,{t_(lo)})*lt(t,{t_(hi)})':eof_action=pass[b{n}]",
    ]


def background_still(
    src: str,
    start: float,
    duration: float,
    geometry: clip_service.ReelGeometry,
    target_aspect_ratio: float = 9 / 16,
) -> Image.Image:
    """Blurred background from the chapter's midpoint frame, exactly canvas-sized.

    ``create_background`` can be a pixel off the canvas for some sizes; like
    MoviePy's compositor, missing area is black and excess is cut.
    """
    frame = ffmpeg_tools.grab_frame(src, start + duration / 2).convert("RGB")
    canvas_w = geometry.canvas_size[0]
    if frame.width > canvas_w:
        # Blur at canvas size, not source size. The height is floored (as
        # create_background floors its own) so a 16:9 frame can't round just
        # above the canvas aspect and lose the cover-scale branch.
        frame = frame.resize(
            (canvas_w, max(1, frame.height * canvas_w // frame.width)),
            Image.LANCZOS,
        )
    bg = clip_service.create_background(frame, target_aspect_ratio)
    if bg.size != geometry.canvas_size:
        bg = bg.crop((0, 0, *geometry.canvas_size))
    return bg


def build_reel_argv(
    src: str,
    output_path: str,
    *,
    start: float,
    duration: float,
    fps: Fraction,
    geometry: clip_service.ReelGeometry,
    background_png: str,
    captions: caption_track.CaptionTrack | None,
    captions_dir: str | None,
    crop_track: CropTrack | None = None,
    broll: Sequence[BrollInsert] | None = None,
) -> list[str]:
    """ffmpeg argv for one reel (see module docstring for the graph).

    ``crop_track`` replaces the letterboxed inset with the moving full-bleed
    crop; ``None`` leaves the graph byte-for-byte as before.

    ``broll`` adds one looped input per insert (after the captions input) and
    overlays them, in start order, between the composite and the captions.
    Timing is validated here (``ValueError``) but files are not touched;
    ``None`` or empty leaves the argv byte-for-byte as before.
    """
    inserts = _normalise_broll(broll, duration)
    canvas_w, _canvas_h = geometry.canvas_size
    fps = grid_rate(fps)
    timebase = math.lcm(fps.numerator, 1000)
    step = fps.denominator * (timebase // fps.numerator)
    if crop_track is None:
        inset, inset_top = f"scale={canvas_w}:-2", inset_y(geometry)
    else:
        inset, inset_top = _pan_chain(geometry, crop_track), 0
    graph = [
        f"[1:v]loop=loop=-1:size=1:start=0,settb=expr=1/{timebase},setpts=N*{step}[bg]",
        f"[0:v]setpts=PTS-STARTPTS,{inset}[in]",
        f"[bg][in]overlay=x=0:y={inset_top}:ts_sync_mode=nearest[b]",
    ]
    inputs = [
        "-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-i", src,
        "-i", background_png,
    ]  # fmt: skip
    if captions is not None:
        if captions_dir is None:
            raise ValueError("captions_dir is required when captions are given")
        inputs += [
            "-f", "concat", "-safe", "0",
            "-i", str(Path(captions_dir) / captions.list_name),
        ]  # fmt: skip
    composite = "b"
    for n, insert in enumerate(inserts):
        index = inputs.count("-i")
        inputs += _broll_input(insert, fps)
        graph += _broll_filters(insert, index, n, composite, fps, geometry)
        composite = f"b{n}"
    if captions is not None:
        graph.append(
            f"[{composite}][2:v]overlay=x={captions.x}:y={captions.y}"
            ":eof_action=pass[c]"
        )
        last = "c"
    else:
        last = composite
    graph.append(f"[{last}]{_EVEN_CROP}[v]")
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        *inputs,
        "-filter_complex", ";".join(graph),
        "-map", "[v]", "-map", "0:a:0?",
        "-t", f"{duration:.6f}", "-r", _rate(fps),
        *_VIDEO_CODEC_ARGS, "-threads", str(_threads()), *_AUDIO_CODEC_ARGS,
        output_path,
    ]  # fmt: skip


def build_trim_argv(
    src: str, output_path: str, *, start: float, duration: float, fps: Fraction
) -> list[str]:
    """ffmpeg argv for a plain re-encoded trim (no reframe, no captions)."""
    fps = grid_rate(fps)
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-i", src,
        "-filter_complex", f"[0:v]setpts=PTS-STARTPTS,{_EVEN_CROP}[v]",
        "-map", "[v]", "-map", "0:a:0?",
        "-t", f"{duration:.6f}", "-r", _rate(fps),
        *_VIDEO_CODEC_ARGS, "-threads", str(_threads()), *_AUDIO_CODEC_ARGS,
        output_path,
    ]  # fmt: skip


# ── Edited timeline (plan) render ─────────────────────────────────────────────

VOICEOVER_DUCK_VOLUME = 0.25
"""Source audio level (about -12 dB) while a voice-over plays."""
_SILENCE = "anullsrc=r=48000:cl=stereo"
_SEGMENT_TOLERANCE = 1e-3
"""Float slack for segments touching each other or the render window."""


@dataclass(frozen=True)
class SourceWindow:
    """``[start, end)`` of the source video, in source seconds."""

    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class OverlayImage:
    """A server-drawn PNG at ``(x, y)`` on the canvas over ``[start, end]``
    (seconds on the edited reel's clock)."""

    path: str
    x: int
    y: int
    start: float
    end: float


@dataclass(frozen=True)
class VoiceOver:
    """An audio file played from ``start`` (reel seconds) for at most
    ``end - start`` seconds, scaled by ``gain``."""

    path: str
    start: float
    end: float
    gain: float = 1.0


def _same_path(a: str, b: str) -> bool:
    if "\x00" in a or "\x00" in b:
        return False
    return os.path.realpath(a) == os.path.realpath(b)


def plan_segments(
    plan: "RenderPlan", video_path: str, span: tuple[float, float] | None = None
) -> list[SourceWindow]:
    """The source windows a plan plays, in order, read from ``video_path``.

    v2: the plan's segments; a plan whose ``source_path`` is another file is
    refused. v1: each video item reads ``[trim_start, trim_start + length)``
    of its ``src``, in reel order (placement gaps close up). A v1 ``src`` is
    a client string, so it is never rendered as given: it must be
    ``video_path`` itself (the job's own source), otherwise ``ValueError``.

    Windows must be non-empty, in source order and not overlap; with
    ``span`` every window must also lie inside it. Raises ``ValueError``.
    """
    if plan.schema_version >= 2:
        if plan.source_path not in (None, "main") and not _same_path(plan.source_path, video_path):
            raise ValueError("the plan reads a different source video than the one being rendered")
        windows = [SourceWindow(seg.start, seg.end) for seg in plan.segments]
    else:
        windows = []
        for item in sorted(plan.video, key=lambda v: v.start):
            if not _same_path(item.src, video_path):
                raise ValueError(
                    f"video item src {item.src!r:.80} is not this job's source video; refusing to render it"
                )
            windows.append(SourceWindow(item.trim_start, item.trim_start + (item.end - item.start)))
    if not windows:
        raise ValueError("the plan has no video segment to render")
    out: list[SourceWindow] = []
    for idx, win in enumerate(windows):
        if not (math.isfinite(win.start) and math.isfinite(win.end)):
            raise ValueError(f"segment {idx}: times are not finite")
        if win.start < 0 or win.end <= win.start:
            raise ValueError(f"segment {idx}: invalid window [{win.start}, {win.end})")
        if span is not None and (
            win.start < span[0] - _SEGMENT_TOLERANCE or win.end > span[1] + _SEGMENT_TOLERANCE
        ):
            raise ValueError(
                f"segment {idx}: [{win.start:g}, {win.end:g}) is outside the render window "
                f"[{span[0]:g}, {span[1]:g})"
            )
        if out and win.start < out[-1].end - _SEGMENT_TOLERANCE:
            raise ValueError(f"segment {idx}: segments must be in source order and must not overlap")
        start = max(win.start, out[-1].end) if out else win.start
        if win.end <= start:
            raise ValueError(f"segment {idx}: empty after its predecessor")
        out.append(SourceWindow(start, win.end))
    return out


def plan_caption_entries(
    captions: Sequence["CaptionItem"], style: CaptionStyle
) -> list[clip_service.CaptionEntry]:
    """Caption schedule for the plan's captions (reel seconds).

    Each caption shows its words (the edited spelling); with a highlighting
    style each word is highlighted from its start until the next word's
    start (the last word until the caption ends), and the caption shows
    plain before its first word. A non-highlighting preset shows the caption
    whole over ``[start, end)``.
    """
    entries: list[clip_service.CaptionEntry] = []
    for cap in captions:
        words = cap.words
        if not words or cap.end <= cap.start:
            continue
        text = " ".join(w.word for w in words)
        if not style.highlights:
            entries.append(clip_service.CaptionEntry(text, None, cap.start, cap.end - cap.start))
            continue
        if words[0].start > cap.start:
            entries.append(clip_service.CaptionEntry(text, None, cap.start, words[0].start - cap.start))
        for j, word in enumerate(words):
            until = words[j + 1].start if j + 1 < len(words) else cap.end
            if until > word.start:
                entries.append(clip_service.CaptionEntry(text, j, word.start, until - word.start))
    return entries


def write_overlay_images(
    overlays: Sequence["TextOverlayItem"], geometry: clip_service.ReelGeometry, out_dir: str | Path
) -> list[OverlayImage]:
    """Draw each text overlay to ``out_dir/overlay_NNN.png`` (server-built
    names) and place it: ``x``/``y`` are the text's centre as fractions of
    the canvas (clamped to [0, 1]), the image is kept inside the canvas and
    its corner is even (4:2:0). Overlays that draw nothing are dropped."""
    canvas_w, canvas_h = geometry.canvas_size
    placed: list[OverlayImage] = []
    for k, item in enumerate(overlays):
        if item.end <= item.start:
            continue
        img = create_text_overlay_image(item.text, item.font_size, item.color)
        if img is None:
            continue

        def corner(frac: float, size: int, canvas: int) -> int:
            centre = min(max(frac, 0.0), 1.0) * canvas
            return even_floor(int(min(max(centre - size / 2, 0), max(canvas - size, 0))))

        path = Path(out_dir) / f"overlay_{k:03d}.png"
        img.save(path, compress_level=1)
        placed.append(
            OverlayImage(
                str(path),
                corner(item.x, img.width, canvas_w),
                corner(item.y, img.height, canvas_h),
                item.start,
                item.end,
            )
        )
    return placed


def _track_value(knots: Sequence[CropKeyframe], t: float, *, left: bool) -> float:
    """``crop_x_expr``'s value at ``t`` (``left``: the limit from below, so a
    hard cut AT ``t`` still gives the value before it)."""
    times = [k[0] for k in knots]
    i = (bisect_left if left else bisect_right)(times, t) - 1
    if i < 0:
        return knots[0][1]
    if i >= len(knots) - 1:
        return knots[-1][1]
    (ta, xa), (tb, xb) = knots[i], knots[i + 1]
    return xa + (xb - xa) * (t - ta) / (tb - ta)


def segment_crop_track(track: CropTrack, offset: float, length: float) -> list[CropKeyframe]:
    """``track`` (t = seconds from the span start) as a segment showing span
    time ``[offset, offset + length)`` sees it from its own t=0.

    The segment's track holds the track's value at its start and end and
    every keyframe strictly between, shifted by ``-offset``, so the pan is
    the same picture over the segment (hard cuts included).
    """
    knots = list(track)
    end = offset + length
    out: list[CropKeyframe] = [(0.0, _track_value(knots, offset, left=False))]
    out += [(min(t - offset, length), x) for t, x in knots if offset < t < end]
    out.append((length, _track_value(knots, end, left=True)))
    if len(out) > MAX_CROP_KEYFRAMES:
        # ponytail: a segment holding nearly all of a full 64-keyframe track
        # plus its two boundary values is thinned evenly (endpoints kept),
        # which can smooth over one pan step; decimating by error (as
        # reframe_service.decimate does) would keep it.
        step = (len(out) - 1) / (MAX_CROP_KEYFRAMES - 1)
        out = [out[round(i * step)] for i in range(MAX_CROP_KEYFRAMES)]
    return out


def build_plan_argv(
    src: str,
    output_path: str,
    *,
    segments: Sequence[SourceWindow],
    fps: Fraction,
    geometry: clip_service.ReelGeometry,
    background_png: str,
    captions: caption_track.CaptionTrack | None,
    captions_dir: str | None,
    source_has_audio: bool,
    mute_source: bool = False,
    crop_track: CropTrack | None = None,
    crop_origin: float = 0.0,
    overlays: Sequence[OverlayImage] = (),
    voiceovers: Sequence[VoiceOver] = (),
) -> list[str]:
    """ffmpeg argv for an edited timeline (module docstring, "Edited
    timeline"). ``segments`` come from ``plan_segments``; ``crop_track``
    times are seconds from ``crop_origin`` (source seconds). Every path is
    an argv element (``-i``), never filtergraph text."""
    if not segments:
        raise ValueError("at least one segment is required")
    canvas_w, _canvas_h = geometry.canvas_size
    fps = grid_rate(fps)
    timebase = math.lcm(fps.numerator, 1000)
    step = fps.denominator * (timebase // fps.numerator)
    n = len(segments)
    span_start = segments[0].start
    span = segments[-1].end - span_start
    total = sum(seg.length for seg in segments)
    use_source_audio = source_has_audio and not mute_source

    def t_(v: float) -> str:
        return _num(v, _TIME_DECIMALS)

    knots: list[CropKeyframe] | None = None
    if crop_track is None:
        inset_top = inset_y(geometry)
    else:
        inset_top = 0
        knots = _normalise_track(crop_track, float(geometry.source_size[0] - pan_crop(geometry).width))

    graph = [f"[1:v]loop=loop=-1:size=1:start=0,settb=expr=1/{timebase},setpts=N*{step}[bg]"]
    inputs = [
        "-ss", f"{span_start:.6f}", "-t", f"{span:.6f}", "-i", src,
        "-i", background_png,
    ]  # fmt: skip
    n_inputs = 2
    video_in, audio_in = ["0:v"], ["0:a"]
    if n > 1:
        video_in = [f"sv{i}" for i in range(n)]
        graph.append(f"[0:v]split={n}" + "".join(f"[{label}]" for label in video_in))
        if use_source_audio:
            audio_in = [f"sa{i}" for i in range(n)]
            graph.append(f"[0:a]asplit={n}" + "".join(f"[{label}]" for label in audio_in))
    pieces: list[str] = []
    for i, seg in enumerate(segments):
        a, b = seg.start - span_start, seg.end - span_start
        if knots is None:
            inset = f"scale={canvas_w}:-2"
        else:
            inset = _pan_chain(geometry, segment_crop_track(knots, seg.start - crop_origin, seg.length))
        graph.append(f"[{video_in[i]}]trim=start={t_(a)}:end={t_(b)},setpts=PTS-STARTPTS,{inset}[g{i}]")
        pieces.append(f"[g{i}]")
        if use_source_audio:
            graph.append(f"[{audio_in[i]}]atrim=start={t_(a)}:end={t_(b)},asetpts=PTS-STARTPTS[h{i}]")
            pieces.append(f"[h{i}]")
    graph.append(
        "".join(pieces)
        + f"concat=n={n}:v=1:a={int(use_source_audio)}[in]"
        + ("[sa]" if use_source_audio else "")
    )
    graph.append(f"[bg][in]overlay=x=0:y={inset_top}:ts_sync_mode=nearest[b]")
    composite = "b"
    if captions is not None:
        if captions_dir is None:
            raise ValueError("captions_dir is required when captions are given")
        inputs += [
            "-f", "concat", "-safe", "0",
            "-i", str(Path(captions_dir) / captions.list_name),
        ]  # fmt: skip
        graph.append(
            f"[b][{n_inputs}:v]overlay=x={captions.x}:y={captions.y}:eof_action=pass[c]"
        )
        n_inputs += 1
        composite = "c"
    for k, ov in enumerate(overlays):
        inputs += ["-i", ov.path]
        graph.append(
            f"[{composite}][{n_inputs}:v]overlay=x={ov.x}:y={ov.y}"
            f":enable='between(t,{t_(ov.start)},{t_(ov.end)})'[o{k}]"
        )
        n_inputs += 1
        composite = f"o{k}"
    graph.append(f"[{composite}]{_EVEN_CROP}[v]")

    audio_out: str | None = None
    if voiceovers:
        labels = []
        for k, vo in enumerate(voiceovers):
            inputs += ["-i", vo.path]
            graph.append(
                f"[{n_inputs}:a]atrim=end={t_(vo.end - vo.start)},asetpts=PTS-STARTPTS,"
                f"volume={_num(vo.gain, 3)},adelay=delays={round(vo.start * 1000)}:all=1[vo{k}]"
            )
            n_inputs += 1
            labels.append(f"[vo{k}]")
        if use_source_audio:
            speaking = "+".join(
                f"between(t,{t_(vo.start)},{t_(vo.end)})" for vo in voiceovers if vo.gain > 0
            )
            duck = (
                f",volume=volume='if({speaking},{_num(VOICEOVER_DUCK_VOLUME, 3)},1)':eval=frame"
                if speaking
                else ""
            )
            graph.append(f"[sa]apad=whole_dur={t_(total)}{duck}[base]")
        else:
            graph.append(f"{_SILENCE},atrim=end={t_(total)}[base]")
        graph.append(
            "[base]" + "".join(labels) + f"amix=inputs={1 + len(labels)}:duration=first:normalize=0[a]"
        )
        audio_out = "[a]"
    elif use_source_audio:
        audio_out = "[sa]"
    elif source_has_audio:  # muted, no voice-over: keep a silent track
        graph.append(f"{_SILENCE},atrim=end={t_(total)}[a]")
        audio_out = "[a]"

    maps = ["-map", "[v]"] + (["-map", audio_out] if audio_out else [])
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        *inputs,
        "-filter_complex", ";".join(graph),
        *maps,
        "-t", f"{total:.6f}", "-r", _rate(fps),
        *_VIDEO_CODEC_ARGS, "-threads", str(_threads()),
        *(_AUDIO_CODEC_ARGS if audio_out else ()),
        output_path,
    ]  # fmt: skip


def _render_plan(
    video_path: str,
    output_path: str,
    start: float,
    end: float,
    plan: "RenderPlan",
    target_aspect_ratio: float,
    crop_track: CropTrack | None,
) -> str:
    segments = plan_segments(plan, video_path, span=(start, end))
    total = sum(seg.length for seg in segments)
    voiceovers = [VoiceOver(a.path, a.start, a.end, a.gain) for a in plan.audio]
    for vo in voiceovers:
        if not Path(vo.path).is_file():
            raise ValueError(f"voice-over {Path(vo.path).name!r} does not exist")
    style = caption_style_for_preset(plan.caption_style)
    entries = plan_caption_entries(plan.captions, style)
    log.info(
        "Plan render  segments=%d  duration=%.3f  captions=%d  overlays=%d  voiceovers=%d  style=%s",
        len(segments), total, len(entries), len(plan.overlays), len(voiceovers), plan.caption_style,
    )
    fps = ffmpeg_tools.fps(video_path)
    source_has_audio = ffmpeg_tools.has_audio(video_path)
    geometry = clip_service.reel_geometry(*ffmpeg_tools.video_size(video_path), target_aspect_ratio)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reelsmith-render-") as work:
        background_png = str(Path(work) / "background.png")
        span_start = segments[0].start
        background_still(
            video_path, span_start, segments[-1].end - span_start, geometry, target_aspect_ratio
        ).save(background_png, compress_level=1)
        track = caption_track.build_caption_track(entries, geometry, total, work, style=style)
        overlays = write_overlay_images(plan.overlays, geometry, work)
        argv = build_plan_argv(
            video_path,
            output_path,
            segments=segments,
            fps=fps,
            geometry=geometry,
            background_png=background_png,
            captions=track,
            captions_dir=work,
            source_has_audio=source_has_audio,
            mute_source=plan.mute_source,
            crop_track=crop_track,
            crop_origin=start,
            overlays=overlays,
            voiceovers=voiceovers,
        )
        _run_atomically(argv, output_path)
    log.info("Render complete  output=%s", output_path)
    return output_path


def _run_atomically(argv: list[str], output_path: str) -> None:
    """Run a render whose argv ends with ``output_path`` without ever leaving a
    partial file there: ffmpeg writes a hidden temp in the same directory,
    which replaces ``output_path`` only on success and is removed on failure.
    """
    out = Path(output_path)
    partial = out.with_name(
        f".{out.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.partial{out.suffix}"
    )
    try:
        ffmpeg_tools.run(
            [*argv[:-1], str(partial)], timeout=settings.render_timeout_seconds
        )
        os.replace(partial, out)
    finally:
        partial.unlink(missing_ok=True)


def render_clip(
    video_path: str,
    output_path: str,
    start: float,
    end: float,
    captions_path: str | None = None,
    target_aspect_ratio: float = 9 / 16,
    word_timings=None,
    caption_words_per_segment: int = 3,
    crop_track: CropTrack | None = None,
    broll: Sequence[BrollInsert] | None = None,
    plan: "RenderPlan | None" = None,
) -> str:
    """Render ``[start, end)`` of ``video_path`` to ``output_path``.

    With ``word_timings`` (karaoke; an empty list means "reel, no captions")
    or a ``captions_path`` (.srt/.vtt), the source is reframed onto a
    ``target_aspect_ratio`` canvas over its blurred background with captions
    burned in. With neither, the chapter is trimmed and re-encoded as is.

    ``crop_track`` (reel renders only) pans a canvas-aspect window of the
    source over time instead of letterboxing it: keyframes are
    ``(seconds from start, crop left edge in source px)``, see
    ``crop_x_expr``. The output size, frame grid and audio are unchanged.

    ``broll`` (reel renders only) shows each ``BrollInsert`` full-canvas over
    its window, under the captions and over the letterbox or pan composite;
    see ``validate_broll`` for the rules (checked before any work starts).
    The output size, frame grid and audio are unchanged.

    ``plan`` (a ``timeline_render_service.RenderPlan``) renders an edited
    timeline as a reel in the same one pass: its segments (which must lie in
    ``[start, end)``) concatenated, its captions in its ``caption_style``,
    its text overlays and voice-overs (module docstring, "Edited timeline").
    ``crop_track`` then holds seconds from ``start`` (computed over the
    whole window) and is re-based per segment. ``captions_path``,
    ``word_timings`` and ``caption_words_per_segment`` are ignored (the
    plan's captions replace them); ``broll`` is refused.
    """
    log.info(
        "Rendering clip %s [%.3f, %.3f] -> %s", video_path, start, end, output_path
    )
    if start < 0 or end <= start:
        raise ValueError(f"invalid render window start={start} end={end}")
    if plan is not None:
        if broll:
            raise ValueError("broll is not supported with an edited timeline plan")
        return _render_plan(
            video_path, output_path, start, end, plan, target_aspect_ratio, crop_track
        )
    duration = end - start
    inserts = validate_broll(broll, duration)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fps = ffmpeg_tools.fps(video_path)

    if word_timings is None and not captions_path:
        if crop_track is not None:
            raise ValueError(
                "crop_track needs a reel render (word_timings or captions_path)"
            )
        if inserts:
            raise ValueError(
                "broll needs a reel render (word_timings or captions_path)"
            )
        log.info("No captions provided; rendering clip without subtitles")
        argv = build_trim_argv(
            video_path, output_path, start=start, duration=duration, fps=fps
        )
        _run_atomically(argv, output_path)
        log.info("Render complete  output=%s", output_path)
        return output_path

    if word_timings is not None:
        log.info(
            "Karaoke render  words=%d  n=%d",
            len(word_timings),
            caption_words_per_segment,
        )
        entries = clip_service.caption_entries(
            word_timings, None, caption_words_per_segment
        )
    else:
        captions = _load_captions(captions_path)
        log.info("Captions loaded  count=%d  path=%s", len(captions), captions_path)
        entries = clip_service.caption_entries(None, captions)

    geometry = clip_service.reel_geometry(
        *ffmpeg_tools.video_size(video_path), target_aspect_ratio
    )
    with tempfile.TemporaryDirectory(prefix="reelsmith-render-") as work:
        background_png = str(Path(work) / "background.png")
        background_still(
            video_path, start, duration, geometry, target_aspect_ratio
        ).save(background_png, compress_level=1)
        track = caption_track.build_caption_track(entries, geometry, duration, work)
        argv = build_reel_argv(
            video_path,
            output_path,
            start=start,
            duration=duration,
            fps=fps,
            geometry=geometry,
            background_png=background_png,
            captions=track,
            captions_dir=work,
            crop_track=crop_track,
            broll=inserts,
        )
        _run_atomically(argv, output_path)
    log.info("Render complete  output=%s", output_path)
    return output_path
