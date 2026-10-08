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
"""

import logging
import math
import os
import tempfile
import uuid
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
) -> list[str]:
    """ffmpeg argv for one reel (see module docstring for the graph)."""
    canvas_w, _canvas_h = geometry.canvas_size
    fps = grid_rate(fps)
    timebase = math.lcm(fps.numerator, 1000)
    step = fps.denominator * (timebase // fps.numerator)
    graph = [
        f"[1:v]loop=loop=-1:size=1:start=0,settb=expr=1/{timebase},setpts=N*{step}[bg]",
        f"[0:v]setpts=PTS-STARTPTS,scale={canvas_w}:-2[in]",
        f"[bg][in]overlay=x=0:y={inset_y(geometry)}:ts_sync_mode=nearest[b]",
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
        graph.append(
            f"[b][2:v]overlay=x={captions.x}:y={captions.y}:eof_action=pass[c]"
        )
        last = "c"
    else:
        last = "b"
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
) -> str:
    """Render ``[start, end)`` of ``video_path`` to ``output_path``.

    With ``word_timings`` (karaoke; an empty list means "reel, no captions")
    or a ``captions_path`` (.srt/.vtt), the source is reframed onto a
    ``target_aspect_ratio`` canvas over its blurred background with captions
    burned in. With neither, the chapter is trimmed and re-encoded as is.
    """
    log.info(
        "Rendering clip %s [%.3f, %.3f] -> %s", video_path, start, end, output_path
    )
    if start < 0 or end <= start:
        raise ValueError(f"invalid render window start={start} end={end}")
    duration = end - start
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fps = ffmpeg_tools.fps(video_path)

    if word_timings is None and not captions_path:
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
        )
        _run_atomically(argv, output_path)
    log.info("Render complete  output=%s", output_path)
    return output_path
