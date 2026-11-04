"""Shared yt-dlp invocation used by every adapter."""
from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor import PostProcessor
from yt_dlp.utils import DownloadCancelled

import app.logging_config  # noqa: F401
from app.services import ffmpeg_tools
from app.services.platforms.base import DownloadResult

log = logging.getLogger(__name__)


# H.264 first: the old pick was whichever codec had the highest bitrate (VP9 or
# AV1), and libaom decodes 1080p AV1 ~10x slower than H.264. The reel canvas is
# 1080 wide, so avc1's 1080p ceiling costs nothing; videos without H.264 fall
# through to the old chain.
_DEFAULT_FORMAT = (
    "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]"
    "/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
)

# Called with the overall download percent (0-100) each time it rises. Set by
# the caller around the download; ``asyncio.to_thread`` copies the context,
# so adapters pass it through without a parameter.
download_progress: ContextVar[Callable[[int], None] | None] = ContextVar(
    "download_progress", default=None
)

# Polled on each progress tick; once it returns True the download is aborted
# with yt-dlp's ``DownloadCancelled`` (a raising progress hook is how yt-dlp
# lets a caller stop it). Set by the orchestrator's download thread, so a
# cancelled job's thread does not keep downloading (spec 013).
download_stop: ContextVar[Callable[[], bool] | None] = ContextVar(
    "download_stop", default=None
)


def _stop_hook(should_stop: Callable[[], bool]) -> Callable[[dict[str, Any]], None]:
    def _hook(status: dict[str, Any]) -> None:
        if should_stop():
            raise DownloadCancelled("the job was cancelled")

    return _hook


class DownloadTracker:
    """Overall percent of one download, fed yt-dlp progress-hook dicts.

    ``bestvideo+bestaudio`` downloads two files one after the other and each
    hook reports one file. Parts are told apart by file name, in the order
    they start, and weighted by the sizes given to ``set_part_sizes``, or
    equally when any size is unknown. The percent never decreases and stays
    within 0-100; ``on_percent`` is called each time its integer value
    changes. Nothing is raised: a raising progress hook aborts the download.
    """

    def __init__(self, on_percent: Callable[[int], None]) -> None:
        self._on_percent = on_percent
        self._sizes: list[int | None] = []
        # File name → fraction downloaded, in the order the parts started.
        self._fractions: dict[str, float] = {}
        self._percent = 0.0
        self._reported = 0

    @property
    def percent(self) -> int:
        return int(self._percent)

    def set_part_sizes(self, sizes: Sequence[int | None]) -> None:
        self._sizes = list(sizes)

    def progress_hook(self, status: dict[str, Any]) -> None:
        try:
            self._update(status)
        except Exception:  # noqa: BLE001 — progress is cosmetic; keep downloading
            log.debug("download progress hook failed", exc_info=True)

    def finish(self) -> None:
        """The download returned: it is complete even if not every part was reported
        (yt-dlp downloads both formats in one call when ffmpeg is the downloader)."""
        try:
            self._raise_to(100.0)
        except Exception:  # noqa: BLE001 — as progress_hook: never fail a finished download
            log.debug("download progress finish failed", exc_info=True)

    def _raise_to(self, percent: float) -> None:
        self._percent = max(self._percent, min(100.0, percent))
        if self.percent != self._reported:
            self._reported = self.percent
            self._on_percent(self._reported)

    def _update(self, status: dict[str, Any]) -> None:
        filename = status.get("filename")
        if not filename:
            return
        if status.get("status") == "finished":
            fraction = 1.0
        elif status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            if not total:
                return
            fraction = (status.get("downloaded_bytes") or 0) / total
        else:
            return
        self._fractions[filename] = min(max(fraction, 0.0), 1.0)
        self._raise_to(100.0 * self._overall())

    def _overall(self) -> float:
        fractions = list(self._fractions.values())
        parts = max(len(self._sizes), len(fractions), 1)
        sizes = self._sizes
        if len(sizes) == parts and all(sizes):
            grand_total = sum(s for s in sizes if s)
            weights = [(s or 0) / grand_total for s in sizes]
        else:
            weights = [1.0 / parts] * parts
        return sum(w * f for w, f in zip(weights, fractions))


class _FormatSizesPP(PostProcessor):
    """Gives the tracker the requested formats' sizes before the download.

    Runs ``before_dl``: yt-dlp drops ``requested_formats`` from the info it
    hands the progress hooks, so they cannot see the other part's size.
    """

    def __init__(self, tracker: DownloadTracker) -> None:
        super().__init__()
        self._tracker = tracker

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        formats = info.get("requested_formats") or [info]
        self._tracker.set_part_sizes(
            [f.get("filesize") or f.get("filesize_approx") for f in formats]
        )
        return [], info


def build_ydl_opts(
    destination_folder: str,
    *,
    format_string: str = _DEFAULT_FORMAT,
    extra_opts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """yt-dlp options for a download into `destination_folder`.

    ``ffmpeg_location`` points yt-dlp at the bundled ffmpeg (constitution III):
    ``bestvideo+bestaudio`` is merged by ffmpeg, which would otherwise be
    whatever ``ffmpeg`` is on ``PATH``. Merging needs ffmpeg only; yt-dlp finds
    no ffprobe next to the bundled binary and does not need one here.
    """
    ydl_opts: dict[str, Any] = {
        "format": format_string,
        "outtmpl": os.path.join(destination_folder, "%(title)s.%(ext)s"),
        "ffmpeg_location": ffmpeg_tools.exe(),
    }
    if extra_opts:
        ydl_opts.update(extra_opts)
    return ydl_opts


def yt_dlp_download(
    url: str,
    destination_folder: str,
    *,
    source: str,
    format_string: str = _DEFAULT_FORMAT,
    extra_opts: dict[str, Any] | None = None,
) -> DownloadResult:
    """Download `url` into `destination_folder` via yt-dlp.

    Reports the overall percent to the ``download_progress`` callback, if one
    is set, and aborts when the ``download_stop`` check, if one is set, turns
    True.

    Raises:
        DownloadCancelled: ``download_stop`` turned True during the download.
    """
    ydl_opts = build_ydl_opts(
        destination_folder, format_string=format_string, extra_opts=extra_opts
    )
    on_percent = download_progress.get()
    should_stop = download_stop.get()

    log.info("[%s] yt-dlp download  url=%s  dest=%s", source, url, destination_folder)
    with YoutubeDL(ydl_opts) as ydl:
        if should_stop is not None:
            ydl.add_progress_hook(_stop_hook(should_stop))
        if on_percent is not None:
            tracker = DownloadTracker(on_percent)
            ydl.add_progress_hook(tracker.progress_hook)
            ydl.add_post_processor(_FormatSizesPP(tracker), when="before_dl")
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        if on_percent is not None:
            tracker.finish()

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
