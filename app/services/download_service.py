import logging
import os

from yt_dlp import YoutubeDL

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)


def download_video(url: str, destination_folder: str) -> tuple[str | None, dict | None]:
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": os.path.join(destination_folder, "%(title)s.%(ext)s"),
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
        return filename, info
    except Exception as e:
        log.error("Error downloading video: %s", e)
        return None, None


def extract_chapters(info: dict) -> list[dict]:
    log.info("Extracting chapters...")
    raw_chapters = info.get("chapters") or []
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
    return parsed
