import logging
import os
from urllib.parse import urlparse

from yt_dlp import YoutubeDL

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)

_SUPPORTED_DOMAINS = frozenset({
    "youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com",
    "vimeo.com", "www.vimeo.com",
    "twitch.tv", "www.twitch.tv", "clips.twitch.tv",
    "loom.com", "www.loom.com",
    "facebook.com", "www.facebook.com", "fb.watch",
    "linkedin.com", "www.linkedin.com",
    "twitter.com", "www.twitter.com", "x.com",
    "rumble.com", "www.rumble.com",
    "dailymotion.com", "www.dailymotion.com",
    "tiktok.com", "www.tiktok.com",
    "instagram.com", "www.instagram.com",
    "reddit.com", "www.reddit.com",
    "streamyard.com", "riverside.fm",
    "drive.google.com",
})


def is_supported_url(url: str) -> bool:
    if url.startswith("upload://"):
        return True
    try:
        host = urlparse(url).hostname or ""
        return host in _SUPPORTED_DOMAINS
    except Exception:  # noqa: BLE001
        return False


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
