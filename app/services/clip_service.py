"""Chapter geometry, caption schedule, audio extraction and background still.

Pure helpers used by the one-pass ffmpeg renderer (``render_service``) and
the orchestrator. Nothing here decodes video frame-by-frame or holds a
composited canvas in memory; the heavy lifting is a single ffmpeg process.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter

import app.logging_config  # noqa: F401
from app.services import ffmpeg_tools

log = logging.getLogger(__name__)


# Chapter ends are kept this far below the shorter of the audio/video streams.
# MoviePy needed it because its chunked audio writer read up to ~1 buffer past
# a subclip's end; ffmpeg does not, but the 1 s guard is kept so chapter
# windows (and therefore published clip lengths) stay exactly as before.
AUDIO_TAIL_EPSILON_SECONDS = 1.0

# Whisper input format: 16 kHz mono signed 16-bit PCM.
_WHISPER_SAMPLE_RATE = 16_000


@dataclass(frozen=True)
class CaptionEntry:
    """One caption image on screen from ``start`` for ``duration`` seconds."""

    text: str
    highlight: int | None
    start: float
    duration: float


@dataclass(frozen=True)
class ReelGeometry:
    """Layout of a reel built from a ``source_size`` source."""

    source_size: tuple[int, int]
    canvas_size: tuple[int, int]
    band_anchor_y: int


def reel_geometry(
    source_w: int, source_h: int, target_aspect_ratio: float = 9 / 16
) -> ReelGeometry:
    """Canvas size and caption anchor for a reel (legacy clip_service.py:186-195).

    The canvas keeps the source width and is ``int(w / ratio)`` tall; captions
    are anchored in the middle of the blur band below the vertically centred
    inset.
    """
    canvas_h = int(source_w / target_aspect_ratio)
    inner_top = (canvas_h - source_h) // 2
    inner_bottom = inner_top + source_h
    band_anchor_y = inner_bottom + (canvas_h - inner_bottom) // 2
    return ReelGeometry(
        source_size=(source_w, source_h),
        canvas_size=(source_w, canvas_h),
        band_anchor_y=band_anchor_y,
    )


def caption_entries(
    word_timings,
    captions=None,
    words_per_segment: int = 3,
) -> list[CaptionEntry]:
    """Caption schedule for a chapter (pure port of legacy clip_service.py:198-227).

    Word timings (karaoke) take precedence; an empty list yields no captions
    and still suppresses ``captions``. Each word shows its group of
    ``words_per_segment`` words with itself highlighted, from its own start
    until the NEXT word's start (the last word until its own end); slots with
    a non-positive duration are skipped but keep their group position.

    Without word timings, ``captions`` (pysrt / webvtt items) are scheduled as
    the legacy code did: from ``caption.start.seconds`` to
    ``caption.end.seconds`` — pysrt's 0-59 *seconds component*, a known legacy
    quirk pinned by the characterization tests.
    """
    entries: list[CaptionEntry] = []
    if word_timings is not None:
        n = words_per_segment
        for i, word in enumerate(word_timings):
            group_start = (i // n) * n
            group = word_timings[group_start : group_start + n]
            group_text = " ".join(w.word for w in group)
            # Extend to the next word's start to avoid inter-word blank frames.
            clip_end = (
                word_timings[i + 1].start if i + 1 < len(word_timings) else word.end
            )
            duration = clip_end - word.start
            if duration <= 0:
                continue
            entries.append(CaptionEntry(group_text, i % n, word.start, duration))
        return entries
    for caption in captions or []:
        start_time = caption.start.seconds
        end_time = caption.end.seconds
        entries.append(
            CaptionEntry(caption.text, None, start_time, end_time - start_time)
        )
    return entries


def probe_safe_end(video_path: str) -> float:
    """Return the highest chapter ``end`` the pipeline will use for this source.

    Takes the minimum of the video and audio stream durations (audio is often
    shorter on re-muxed YouTube downloads) minus AUDIO_TAIL_EPSILON_SECONDS.
    """
    v_dur = ffmpeg_tools.duration(video_path, "video")
    a_dur = ffmpeg_tools.duration(video_path, "audio")
    if v_dur is None:
        raise ValueError(f"{video_path}: no video stream")
    if a_dur is None:
        a_dur = v_dur
    return max(0.0, min(v_dur, a_dur) - AUDIO_TAIL_EPSILON_SECONDS)


def extract_audio_argv(
    src: str, start: float, duration: float, wav_path: str
) -> list[str]:
    """ffmpeg argv for ``extract_audio`` (same ``-ss``/``-t`` window as the render)."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.6f}",
        "-t",
        f"{duration:.6f}",
        "-i",
        src,
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(_WHISPER_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        wav_path,
    ]


def extract_audio(src: str, start: float, duration: float, wav_path: str) -> str | None:
    """Write ``[start, start + duration)`` of ``src``'s audio as 16 kHz mono PCM.

    Uses the render's exact ``-ss start -t duration`` input window, so word
    timestamps from this file line up sample-accurately with the rendered
    reel's audio. Returns ``wav_path``, or ``None`` when ``src`` has no audio
    stream (nothing is written).
    """
    if start < 0 or duration <= 0:
        raise ValueError(f"invalid audio window start={start} duration={duration}")
    if not ffmpeg_tools.has_audio(src):
        log.warning("No audio stream in %s; skipping audio extraction", src)
        return None
    Path(wav_path).parent.mkdir(parents=True, exist_ok=True)
    log.info(
        "Extracting audio  [%.3f, +%.3f]  src=%s -> %s", start, duration, src, wav_path
    )
    ffmpeg_tools.run(extract_audio_argv(src, start, duration, wav_path))
    return wav_path


def create_background(frame: Image.Image, target_aspect_ratio: float = 9 / 16):
    """Return the blurred still behind the inset (PIL logic of legacy :144-172).

    ``frame`` is one representative source frame (the chapter midpoint); the
    result is ``frame.width`` x ``int(frame.width / ratio)``, scaled to cover,
    centre-cropped and Gaussian-blurred.
    """
    log.info("Creating blurred background...")
    target_height = int(frame.width / target_aspect_ratio)
    target_width = frame.width

    pil = frame

    # Scale so the shorter dimension fills the target canvas.
    src_w, src_h = pil.size
    if src_h / src_w > target_aspect_ratio:
        scale = target_width / src_w
    else:
        scale = target_height / src_h
    scaled = pil.resize((int(src_w * scale), int(src_h * scale)), Image.LANCZOS)

    # Centre-crop to exact canvas size.
    cx, cy = scaled.width / 2, scaled.height / 2
    box = (
        int(cx - target_width / 2),
        int(cy - target_height / 2),
        int(cx + target_width / 2),
        int(cy + target_height / 2),
    )
    cropped = scaled.crop(box)

    return cropped.filter(ImageFilter.GaussianBlur(40))
