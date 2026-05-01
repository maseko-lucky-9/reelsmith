import logging
import os

from yt_dlp import YoutubeDL

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)


def fetch_video_title(video_url: str) -> str:
    ydl_opts = {"quiet": True, "extract_flat": True, "no_warnings": True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        return info["title"]


def create_video_subfolder(download_path: str, video_url: str) -> tuple[str, str]:
    try:
        title = fetch_video_title(video_url).replace(" ", "_")
        video_folder_path = os.path.join(download_path, title)
    except Exception as e:
        log.warning("Falling back to generic folder name: %s", e)
        video_folder_path = os.path.join(download_path, "youtube_video")

    clips_folder_path = os.path.join(video_folder_path, "clips")
    os.makedirs(video_folder_path, exist_ok=True)
    os.makedirs(clips_folder_path, exist_ok=True)
    return video_folder_path, clips_folder_path
