"""TikTok adapter — short-form, no chapters.

Public posts only. Watermark may be present in the source; removal is out
of scope for v1.
"""
from __future__ import annotations

from app.services.platforms._yt_dlp_base import yt_dlp_download
from app.services.platforms.base import Chapter, DownloadResult, host_matches


class TikTokAdapter:
    platform_id = "tiktok"

    @classmethod
    def matches(cls, url: str) -> bool:
        return host_matches(url, "tiktok.com")

    def download(self, url: str, destination_folder: str) -> DownloadResult:
        return yt_dlp_download(
            url, destination_folder, source=self.platform_id, format_string="best"
        )

    def extract_chapters(self, info: dict) -> list[Chapter]:
        return []
