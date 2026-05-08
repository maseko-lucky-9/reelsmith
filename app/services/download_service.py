"""Backward-compat shims.

The real implementation lives under `app.services.platforms`. These shims
preserve the previous module-level API so existing tests and callers keep
working during the multi-platform migration.

`upload://` is preserved as a special case because it represents an internal
uploaded-video path, not a remote platform.
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from app.services.platforms import (
    UnsupportedPlatformError,
    YouTubeAdapter,
    detect_platform_id,
)

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)


def is_supported_url(url: str) -> bool:
    if not isinstance(url, str) or not url:
        return False
    if url.startswith("upload://"):
        return True
    try:
        return detect_platform_id(url) is not None
    except Exception:  # noqa: BLE001
        return False


def download_video(url: str, destination_folder: str) -> tuple[str | None, dict | None]:
    """Deprecated. Use `app.services.platforms.resolve(url).download(...)` instead."""
    log.info("download_service.download_video(...) is a shim — use platform adapters")
    try:
        result = YouTubeAdapter().download(url, destination_folder)
        return result.video_path, result.info
    except Exception as e:  # noqa: BLE001
        log.error("Download failed  url=%s  error=%s", url, e)
        return None, None


def extract_chapters(info: dict) -> list[dict]:
    """Deprecated. Use `YouTubeAdapter().extract_chapters(info)` instead."""
    chapters = YouTubeAdapter().extract_chapters(info)
    return [asdict(c) for c in chapters]
