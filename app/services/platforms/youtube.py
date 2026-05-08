"""YouTube adapter — full chapter-aware ingestion."""
from __future__ import annotations

from app.services.platforms._yt_dlp_base import yt_dlp_download
from app.services.platforms.base import (
    Chapter,
    DownloadResult,
    host_matches,
)


class YouTubeAdapter:
    platform_id = "youtube"

    @classmethod
    def matches(cls, url: str) -> bool:
        return host_matches(url, "youtube.com", "youtu.be")

    def download(self, url: str, destination_folder: str) -> DownloadResult:
        return yt_dlp_download(url, destination_folder, source=self.platform_id)

    def extract_chapters(self, info: dict) -> list[Chapter]:
        raw = info.get("chapters") or []
        return [
            Chapter(
                index=i,
                title=c["title"],
                start=float(c["start_time"]),
                end=float(c["end_time"]),
            )
            for i, c in enumerate(raw)
        ]
