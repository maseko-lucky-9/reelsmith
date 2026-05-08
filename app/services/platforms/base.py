"""Platform adapter base types and helpers.

Each adapter encapsulates how a single video platform is downloaded and how
its chapter metadata is interpreted. Adapters are stateless; the registry
instantiates them per-job via `cls()`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


@dataclass
class DownloadResult:
    video_path: str
    info: dict
    title: str
    duration: float
    source: str  # platform_id


@dataclass
class Chapter:
    index: int
    title: str
    start: float
    end: float


class PlatformAdapter(Protocol):
    platform_id: str

    @classmethod
    def matches(cls, url: str) -> bool: ...

    def download(self, url: str, destination_folder: str) -> DownloadResult: ...

    def extract_chapters(self, info: dict) -> list[Chapter]: ...


def host_matches(url: str, *hosts: str) -> bool:
    """Return True if `url`'s host equals one of `hosts` (or a subdomain).

    Anchored on `^https?://` to prevent confusable strings (`eviltiktok.com`,
    `youtube.com.attacker.io`).
    """
    if not url:
        return False
    pattern = (
        r"^https?://(?:[^/]+\.)?(?:"
        + "|".join(re.escape(h) for h in hosts)
        + r")(?:/|$)"
    )
    return re.match(pattern, url) is not None
