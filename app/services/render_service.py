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
"""

import logging
import math
import os
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import pysrt
from PIL import Image
from webvtt import WebVTT

from app.services import caption_track, clip_service, ffmpeg_tools
from app.settings import settings

import app.logging_config  # noqa: F401

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
    return even_floor((geometry.canvas_size[1] - geometry.source_size[1]) // 2)


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
    """
    log.info(
        "Rendering clip %s [%.3f, %.3f] -> %s", video_path, start, end, output_path
    )
    if start < 0 or end <= start:
        raise ValueError(f"invalid render window start={start} end={end}")
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
