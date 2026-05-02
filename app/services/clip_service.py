import logging
from contextlib import contextmanager

import numpy as np
from moviepy.editor import CompositeVideoClip, ImageClip, VideoFileClip
from PIL import Image, ImageFilter

from app.services.subtitle_image_service import create_subtitle_image

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)


@contextmanager
def closing_clip(path: str):
    clip = VideoFileClip(path)
    try:
        yield clip
    finally:
        try:
            if clip.audio is not None:
                clip.audio.close()
        except Exception:
            pass
        try:
            clip.close()
        except Exception:
            pass


def create_clip(video, start_time: float, end_time: float):
    if start_time is None or end_time is None:
        raise ValueError("start_time and end_time are required")
    if start_time < 0 or end_time < 0:
        raise ValueError("start_time and end_time must be non-negative")
    if start_time >= end_time:
        raise ValueError("start_time must be less than end_time")
    log.info("Creating subclip [%.3f, %.3f]", start_time, end_time)
    return video.subclip(start_time, end_time)


def extract_chapter_to_disk(
    video_path: str,
    start: float,
    end: float,
    out_clip_path: str,
    out_audio_path: str,
) -> tuple[str, str]:
    """Slice a chapter from a source video and persist clip + audio to disk."""
    log.info("Extracting chapter to disk  [%.3f, %.3f]  src=%s", start, end, video_path)
    with closing_clip(video_path) as video:
        sub = create_clip(video, start, end)
        try:
            sub.write_videofile(
                out_clip_path,
                codec="libx264",
                audio_codec="aac",
                logger=None,
            )
            if sub.audio is not None:
                sub.audio.write_audiofile(out_audio_path, logger=None)
        finally:
            try:
                if sub.audio is not None:
                    sub.audio.close()
            except Exception:
                pass
            try:
                sub.close()
            except Exception:
                pass
    log.info("Chapter extracted  clip=%s  audio=%s", out_clip_path, out_audio_path)
    return out_clip_path, out_audio_path


def create_subtitle_clip(text: str, videosize, duration: float, highlight_word_index: int | None = None):
    log.info("Create Subtitle Clip...")
    subtitle_image = create_subtitle_image(text, videosize, highlight_word_index=highlight_word_index)
    return ImageClip(subtitle_image).set_duration(duration)


def _blur_frame(image, blur_radius: int = 5):
    return np.array(
        Image.fromarray(image).filter(ImageFilter.GaussianBlur(blur_radius))
    )


def create_background(clip, target_aspect_ratio: float = 9 / 16):
    """Return a static blurred ImageClip the size of the target frame.

    Blurring a single representative frame (mid-clip) rather than every frame
    via fl_image cuts render time dramatically — the background is effectively
    a still image, so motion accuracy is not needed.
    """
    log.info("Creating blurred background...")
    target_height = int(clip.w / target_aspect_ratio)
    target_width = clip.w

    # Sample one frame near the middle of the clip for the background.
    sample_t = clip.duration / 2
    frame = clip.get_frame(sample_t)
    pil = Image.fromarray(frame)

    # Scale so the shorter dimension fills the target canvas.
    src_w, src_h = pil.size
    if src_h / src_w > target_aspect_ratio:
        scale = target_width / src_w
    else:
        scale = target_height / src_h
    scaled = pil.resize(
        (int(src_w * scale), int(src_h * scale)), Image.LANCZOS
    )

    # Centre-crop to exact canvas size.
    cx, cy = scaled.width / 2, scaled.height / 2
    box = (
        int(cx - target_width / 2),
        int(cy - target_height / 2),
        int(cx + target_width / 2),
        int(cy + target_height / 2),
    )
    cropped = scaled.crop(box)

    blurred = np.array(cropped.filter(ImageFilter.GaussianBlur(40)))
    return ImageClip(blurred).set_duration(clip.duration)


def add_captions_to_clip(
    clip,
    captions,
    target_aspect_ratio: float = 9 / 16,
    word_timings=None,
    caption_words_per_segment: int = 3,
):
    log.info("Add Captions To Clip...")
    background = create_background(clip, target_aspect_ratio)

    new_height = int(clip.w / target_aspect_ratio)
    new_size = (clip.w, new_height)

    resized_clip = clip.resize(height=clip.h)
    resized_clip = resized_clip.set_position(("center", "center"))

    subtitle_clips = []
    if word_timings is not None:
        n = caption_words_per_segment
        for i, word in enumerate(word_timings):
            group_idx = i // n
            word_pos = i % n
            group_start = group_idx * n
            group = word_timings[group_start:group_start + n]
            group_text = " ".join(w.word for w in group)

            # Extend clip to next word's start to avoid inter-word blank frames.
            clip_end = word_timings[i + 1].start if i + 1 < len(word_timings) else word.end
            duration = clip_end - word.start
            if duration <= 0:
                continue

            subtitle_clip = create_subtitle_clip(
                group_text, clip.size, duration, highlight_word_index=word_pos
            )
            subtitle_clips.append(subtitle_clip.set_start(word.start))
    else:
        for caption in captions:
            start_time = caption.start.seconds
            end_time = caption.end.seconds
            duration = end_time - start_time
            subtitle_clip = create_subtitle_clip(caption.text, clip.size, duration)
            subtitle_clips.append(subtitle_clip.set_start(start_time))

    main_clip_with_subtitles = CompositeVideoClip(
        [resized_clip] + subtitle_clips, size=clip.size
    )
    final_clip = CompositeVideoClip(
        [background, main_clip_with_subtitles.set_position(("center", "center"))],
        size=new_size,
    )
    return final_clip
