import logging
import os
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from yt_dlp import YoutubeDL

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)


_SLUG_RE = re.compile(r"[^\w\-]")

# One folder per source platform, so a job is easy to find by where it came
# from. Upload, generate and unknown sources stay directly under the base.
SOURCE_FOLDERS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "youtube": "YouTube Videos",
        "tiktok": "TikTok Videos",
        "facebook": "Facebook Videos",
        "instagram": "Instagram Videos",
    }
)


def source_folder(platform_id: str | None) -> str | None:
    """The group folder name for a platform id, or ``None`` if it has none."""
    return SOURCE_FOLDERS.get(platform_id or "")


def _slugify(name: str, fallback: str) -> str:
    """Sanitise a title to a filesystem-safe slug, capped at 80 chars."""
    cleaned = _SLUG_RE.sub("_", name).strip("_")
    return cleaned[:80] or fallback


def fetch_video_title(video_url: str) -> str:
    # socket_timeout bounds each network read: this runs in a worker thread
    # (asyncio.to_thread), which no asyncio timeout can stop.
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "no_warnings": True,
        "socket_timeout": 10,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        return info["title"]


def create_video_subfolder(
    download_path: str,
    video_url: str,
    platform_id: str = "video",
    job_id: str | None = None,
) -> tuple[str, str]:
    """Create ``<download_path>/[<group>/]<slug>[-<job8>]/clips``; return both paths.

    ``<group>`` is the platform's folder from ``SOURCE_FOLDERS`` (for example
    ``TikTok Videos``); platforms without one, and uploads, sit directly under
    ``download_path``.

    With ``job_id`` the folder name ends in the first 8 characters of the job
    id, so two jobs never share an output folder: ``upload://`` and
    ``generate://`` sources all map to one constant slug, and two videos can
    share a title. Without it (the job-less ``POST /folders``) the name is the
    bare slug, as before.
    """
    log.info(
        "Resolving folder  url=%s  base=%s  platform=%s  job=%s",
        video_url, download_path, platform_id, job_id,
    )
    fallback = f"{platform_id}_video"
    # Only yt-dlp can resolve a title for real http(s) URLs. Internal schemes
    # (generate://, upload://) would otherwise stall ~10s inside extract_info
    # before the except fallback — skip straight to the generic slug for them.
    if not video_url.startswith(("http://", "https://")):
        slug = fallback
    else:
        try:
            title = fetch_video_title(video_url)
            slug = _slugify(title, fallback)
        except Exception as e:
            log.warning("Falling back to generic folder name (%s): %s", fallback, e)
            slug = fallback
    if job_id:
        slug = f"{slug}-{job_id[:8]}"

    group = source_folder(platform_id)
    base = os.path.join(download_path, group) if group else download_path
    video_folder_path = os.path.join(base, slug)
    clips_folder_path = os.path.join(video_folder_path, "clips")
    os.makedirs(video_folder_path, exist_ok=True)
    os.makedirs(clips_folder_path, exist_ok=True)
    log.info("Folders created  video=%s  clips=%s", video_folder_path, clips_folder_path)
    return video_folder_path, clips_folder_path
