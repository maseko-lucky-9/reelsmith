"""Social-publish platform adapters (W1.5).

Per ADR-003 §A.1: stub provider is the default. YouTube ships the
sole live OAuth path in Wave 1; TikTok / IG / LinkedIn / X carry the
adapter shell but require app-review before going live (see
``docs/social-publish-onboarding.md``).
"""
from __future__ import annotations

from app.services.social.base import (
    PlatformAdapter,
    PublishRequest,
    PublishResult,
    UnsupportedPlatformError,
)
from app.services.social.registry import get_adapter, supported_platforms

__all__ = [
    "PlatformAdapter",
    "PublishRequest",
    "PublishResult",
    "UnsupportedPlatformError",
    "get_adapter",
    "supported_platforms",
]
