"""Instagram adapter — short-form, no chapters.

Public posts/reels only. Many Instagram videos require login; those will
fail at yt-dlp level and surface as a download error. Authentication is
out of scope for v1.
"""
from __future__ import annotations

from app.services.platforms._yt_dlp_base import yt_dlp_download
from app.services.platforms.base import Chapter, DownloadResult, host_matches


class InstagramAdapter:
    platform_id = "instagram"

    @classmethod
    def matches(cls, url: str) -> bool:
        return host_matches(url, "instagram.com", "instagr.am")

    def download(self, url: str, destination_folder: str) -> DownloadResult:
        return yt_dlp_download(
            url, destination_folder, source=self.platform_id, format_string="best"
        )

    def extract_chapters(self, info: dict) -> list[Chapter]:
        return []
