import logging
import os

from yt_dlp import YoutubeDL

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)


def download_video(url: str, destination_folder: str) -> tuple[str | None, dict | None]:
    log.info("Downloading  url=%s  dest=%s", url, destination_folder)
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": os.path.join(destination_folder, "%(title)s.%(ext)s"),
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
        log.info("Download success  title=%r  duration=%ss  file=%s",
                 info.get("title"), info.get("duration"), filename)
        return filename, info
    except Exception as e:
        log.error("Download failed  url=%s  error=%s", url, e)
        return None, None


def extract_chapters(info: dict) -> list[dict]:
    raw_chapters = info.get("chapters") or []
    log.info("Extracting chapters  raw_count=%d", len(raw_chapters))
    parsed = []
    for index, chapter in enumerate(raw_chapters):
        parsed.append(
            {
                "index": index,
                "title": chapter["title"],
                "start": chapter["start_time"],
                "end": chapter["end_time"],
            }
        )
        log.debug("  chapter %d: %r  %.1f–%.1fs", index, chapter["title"],
                  chapter["start_time"], chapter["end_time"])
    return parsed
