import logging
from pathlib import Path

import pysrt
from webvtt import WebVTT

from app.services.clip_service import add_captions_to_clip, closing_clip

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)


def _load_captions(captions_path: str):
    suffix = Path(captions_path).suffix.lower()
    if suffix == ".srt":
        return pysrt.open(captions_path)
    if suffix == ".vtt":
        return list(WebVTT().read(captions_path))
    raise ValueError(f"Unsupported captions extension: {suffix}")


def render_clip(
    video_path: str,
    output_path: str,
    start: float,
    end: float,
    captions_path: str | None = None,
    target_aspect_ratio: float = 9 / 16,
) -> str:
    log.info("Rendering clip %s [%.3f, %.3f] -> %s", video_path, start, end, output_path)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with closing_clip(video_path) as video:
        sub = video.subclip(start, end)
        try:
            if captions_path:
                captions = _load_captions(captions_path)
                log.info("Captions loaded  count=%d  path=%s", len(captions), captions_path)
                final = add_captions_to_clip(sub, captions, target_aspect_ratio)
            else:
                log.info("No captions provided; rendering clip without subtitles")
                final = sub
            import os
            cpu_count = os.cpu_count() or 2
            final.write_videofile(
                output_path,
                codec="libx264",
                audio_codec="aac",
                preset="ultrafast",   # much faster encode; fine for review clips
                ffmpeg_params=["-crf", "28"],  # slightly lower quality, faster
                threads=min(cpu_count, 8),
                logger=None,
            )
        finally:
            try:
                sub.close()
            except Exception:
                pass
    log.info("Render complete  output=%s", output_path)
    return output_path
