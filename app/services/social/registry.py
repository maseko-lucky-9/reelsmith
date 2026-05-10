"""Adapter registry — resolves platform name + provider to an adapter.

Default provider is ``stub`` for every platform. The operator opts
into live adapters by setting ``YTVIDEO_SOCIAL_PROVIDER=real`` (or
per-platform: ``YTVIDEO_SOCIAL_PROVIDER_YOUTUBE=real``).
"""
from __future__ import annotations

import os

from app.services.social.base import PlatformAdapter, UnsupportedPlatformError
from app.services.social.stub import StubAdapter

SUPPORTED: tuple[str, ...] = ("youtube", "tiktok", "instagram", "linkedin", "x")


def supported_platforms() -> tuple[str, ...]:
    return SUPPORTED


def _provider_for(platform: str) -> str:
    per_platform = os.environ.get(f"YTVIDEO_SOCIAL_PROVIDER_{platform.upper()}", "")
    if per_platform:
        return per_platform.strip().lower()
    return os.environ.get("YTVIDEO_SOCIAL_PROVIDER", "stub").strip().lower()


def get_adapter(platform: str) -> PlatformAdapter:
    if platform not in SUPPORTED:
        raise UnsupportedPlatformError(f"unknown platform: {platform!r}")

    provider = _provider_for(platform)
    if provider == "stub":
        return StubAdapter(platform=platform)

    # Live providers — only YouTube is shipped as live in W1.5.
    if platform == "youtube":
        from app.services.social.youtube import YouTubeAdapter
        return YouTubeAdapter()

    # Live adapter not yet implemented for this platform → fall back to stub
    # so the dashboard still works while operators complete app review.
    return StubAdapter(platform=platform)
