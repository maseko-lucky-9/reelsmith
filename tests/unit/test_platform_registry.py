"""Registry resolution: URL → adapter."""
from __future__ import annotations

import pytest

from app.services.platforms import (
    FacebookAdapter,
    InstagramAdapter,
    TikTokAdapter,
    UnsupportedPlatformError,
    YouTubeAdapter,
    detect_platform_id,
    resolve,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc",
        "https://youtube.com/watch?v=abc",
        "https://m.youtube.com/watch?v=abc",
        "https://youtu.be/abc123",
    ],
)
def test_resolve_youtube_url(url):
    assert isinstance(resolve(url), YouTubeAdapter)
    assert detect_platform_id(url) == "youtube"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/reel/123456",
        "https://www.facebook.com/watch/?v=123",
        "https://m.facebook.com/watch/?v=123",
        "https://fb.watch/abc/",
    ],
)
def test_resolve_facebook_url(url):
    assert isinstance(resolve(url), FacebookAdapter)
    assert detect_platform_id(url) == "facebook"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.tiktok.com/@user/video/123",
        "https://tiktok.com/@user/video/123",
        "https://vm.tiktok.com/abc/",
    ],
)
def test_resolve_tiktok_url(url):
    assert isinstance(resolve(url), TikTokAdapter)
    assert detect_platform_id(url) == "tiktok"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/reel/abc/",
        "https://www.instagram.com/p/abc/",
        "https://instagram.com/reel/abc/",
        "https://instagr.am/p/abc/",
    ],
)
def test_resolve_instagram_url(url):
    assert isinstance(resolve(url), InstagramAdapter)
    assert detect_platform_id(url) == "instagram"


def test_resolve_unsupported_raises():
    with pytest.raises(UnsupportedPlatformError) as exc:
        resolve("https://example.com/video.mp4")
    assert exc.value.url == "https://example.com/video.mp4"


def test_detect_platform_id_returns_none_for_unsupported():
    assert detect_platform_id("https://example.com/foo") is None


def test_resolve_rejects_confusable_hostnames():
    """Anchored regex must not match `eviltiktok.com` or `youtube.com.attacker.io`."""
    with pytest.raises(UnsupportedPlatformError):
        resolve("https://eviltiktok.com/foo")
    with pytest.raises(UnsupportedPlatformError):
        resolve("https://youtube.com.attacker.io/watch")


def test_resolve_rejects_empty_and_malformed():
    with pytest.raises(UnsupportedPlatformError):
        resolve("")
    with pytest.raises(UnsupportedPlatformError):
        resolve("not-a-url")
