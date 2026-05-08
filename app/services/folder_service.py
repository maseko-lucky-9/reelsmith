import logging
import os
import re

from yt_dlp import YoutubeDL

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)


_SLUG_RE = re.compile(r"[^\w\-]")


def _slugify(name: str, fallback: str) -> str:
    """Sanitise a title to a filesystem-safe slug, capped at 80 chars."""
    cleaned = _SLUG_RE.sub("_", name).strip("_")
    return cleaned[:80] or fallback


def fetch_video_title(video_url: str) -> str:
    ydl_opts = {"quiet": True, "extract_flat": True, "no_warnings": True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        return info["title"]


def create_video_subfolder(
    download_path: str,
    video_url: str,
    platform_id: str = "video",
) -> tuple[str, str]:
    log.info(
        "Resolving folder  url=%s  base=%s  platform=%s",
        video_url, download_path, platform_id,
    )
    fallback = f"{platform_id}_video"
    try:
        title = fetch_video_title(video_url)
        slug = _slugify(title, fallback)
    except Exception as e:
        log.warning("Falling back to generic folder name (%s): %s", fallback, e)
        slug = fallback

    video_folder_path = os.path.join(download_path, slug)
    clips_folder_path = os.path.join(video_folder_path, "clips")
    os.makedirs(video_folder_path, exist_ok=True)
    os.makedirs(clips_folder_path, exist_ok=True)
    log.info("Folders created  video=%s  clips=%s", video_folder_path, clips_folder_path)
    return video_folder_path, clips_folder_path
