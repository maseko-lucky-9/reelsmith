"""Shared yt-dlp invocation used by every adapter."""
from __future__ import annotations

import logging
import os
from typing import Any

from yt_dlp import YoutubeDL

from app.services.platforms.base import DownloadResult

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)


_DEFAULT_FORMAT = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"


def yt_dlp_download(
    url: str,
    destination_folder: str,
    *,
    source: str,
    format_string: str = _DEFAULT_FORMAT,
    extra_opts: dict[str, Any] | None = None,
) -> DownloadResult:
    """Download `url` into `destination_folder` via yt-dlp."""
    ydl_opts: dict[str, Any] = {
        "format": format_string,
        "outtmpl": os.path.join(destination_folder, "%(title)s.%(ext)s"),
    }
    if extra_opts:
        ydl_opts.update(extra_opts)

    log.info("[%s] yt-dlp download  url=%s  dest=%s", source, url, destination_folder)
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)

    title = info.get("title") or ""
    duration = float(info.get("duration") or 0.0)
    log.info(
        "[%s] download success  title=%r  duration=%.1fs  file=%s",
        source, title, duration, filename,
    )
    return DownloadResult(
        video_path=filename,
        info=info,
        title=title,
        duration=duration,
        source=source,
    )
