"""Platform adapter registry.

Resolves a URL to a `PlatformAdapter` implementation. Each adapter
encapsulates its own download options and chapter-extraction policy.

To add a new platform: implement a class satisfying `PlatformAdapter` and
append it to `_ADAPTERS`. Frontend mirror lives at
`web/src/lib/detectPlatform.ts` — keep regex hosts in sync.
"""
from __future__ import annotations

from app.services.platforms.base import (
    Chapter,
    DownloadResult,
    PlatformAdapter,
)
from app.services.platforms.facebook import FacebookAdapter
from app.services.platforms.instagram import InstagramAdapter
from app.services.platforms.tiktok import TikTokAdapter
from app.services.platforms.youtube import YouTubeAdapter


class UnsupportedPlatformError(ValueError):
    def __init__(self, url: str):
        super().__init__(f"No adapter matches URL: {url}")
        self.url = url


_ADAPTERS: list[type[PlatformAdapter]] = [
    YouTubeAdapter,
    FacebookAdapter,
    TikTokAdapter,
    InstagramAdapter,
]


def resolve(url: str) -> PlatformAdapter:
    for cls in _ADAPTERS:
        if cls.matches(url):
            return cls()
    raise UnsupportedPlatformError(url)


def detect_platform_id(url: str) -> str | None:
    for cls in _ADAPTERS:
        if cls.matches(url):
            return cls.platform_id
    return None


__all__ = [
    "Chapter",
    "DownloadResult",
    "FacebookAdapter",
    "InstagramAdapter",
    "PlatformAdapter",
    "TikTokAdapter",
    "UnsupportedPlatformError",
    "YouTubeAdapter",
    "detect_platform_id",
    "resolve",
]
