"""Facebook adapter — short-form, no chapters.

Public posts/reels only. Authenticated content (login-walled videos) is
out of scope for v1.
"""
from __future__ import annotations

from app.services.platforms._yt_dlp_base import yt_dlp_download
from app.services.platforms.base import Chapter, DownloadResult, host_matches


class FacebookAdapter:
    platform_id = "facebook"

    @classmethod
    def matches(cls, url: str) -> bool:
        return host_matches(url, "facebook.com", "fb.watch")

    def download(self, url: str, destination_folder: str) -> DownloadResult:
        # Facebook reels often serve a single combined stream; `best` avoids
        # spurious "no separate bestvideo+bestaudio" yt-dlp warnings.
        return yt_dlp_download(
            url, destination_folder, source=self.platform_id, format_string="best"
        )

    def extract_chapters(self, info: dict) -> list[Chapter]:
        return []
