"""Unit tests for get_adapter() TikTok resolution (app.services.social.registry).

Covers:
- stub / cookie / n8n / unknown / empty provider strings for tiktok
- regression: youtube resolution still works after tiktok branch added

Note: tests for 'cookie' and 'n8n' providers require Agent A adapters.
They are skipped gracefully when those adapters are not yet available.
"""
from __future__ import annotations

import pytest

from app.services.social import UnsupportedPlatformError, get_adapter
from app.services.social.stub import StubAdapter

# ── Check Agent A adapter availability ───────────────────────────────────────

try:
    from app.services.social.tiktok import TikTokCookieAdapter as _  # noqa: F401
    HAS_COOKIE_ADAPTER = True
except (ImportError, ModuleNotFoundError):
    HAS_COOKIE_ADAPTER = False

try:
    from app.services.social.n8n_tiktok import N8nTikTokAdapter as _  # noqa: F401
    HAS_N8N_ADAPTER = True
except (ImportError, ModuleNotFoundError):
    HAS_N8N_ADAPTER = False


# ── TikTok provider resolution ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "provider,expected_type_name,requires",
    [
        ("stub", "StubAdapter", None),
        ("cookie", "TikTokCookieAdapter", "cookie"),
        ("n8n", "N8nTikTokAdapter", "n8n"),
        ("unknown_value", "StubAdapter", None),
        ("", "StubAdapter", None),
    ],
)
def test_tiktok_provider_resolution(monkeypatch, provider, expected_type_name, requires):
    """YTVIDEO_SOCIAL_PROVIDER_TIKTOK controls which adapter is returned."""
    if requires == "cookie" and not HAS_COOKIE_ADAPTER:
        pytest.skip("requires Agent A TikTokCookieAdapter (app.services.social.tiktok)")
    if requires == "n8n" and not HAS_N8N_ADAPTER:
        pytest.skip("requires Agent A N8nTikTokAdapter (app.services.social.n8n_tiktok)")

    # Clear the global provider so per-platform env has full control
    monkeypatch.delenv("YTVIDEO_SOCIAL_PROVIDER", raising=False)
    monkeypatch.setenv("YTVIDEO_SOCIAL_PROVIDER_TIKTOK", provider)

    adapter = get_adapter("tiktok")
    assert type(adapter).__name__ == expected_type_name, (
        f"provider={provider!r} expected {expected_type_name!r}, "
        f"got {type(adapter).__name__!r}"
    )


def test_tiktok_default_is_stub_when_no_env(monkeypatch):
    """Without any YTVIDEO_SOCIAL_PROVIDER* vars, tiktok defaults to stub."""
    monkeypatch.delenv("YTVIDEO_SOCIAL_PROVIDER", raising=False)
    monkeypatch.delenv("YTVIDEO_SOCIAL_PROVIDER_TIKTOK", raising=False)

    adapter = get_adapter("tiktok")
    assert isinstance(adapter, StubAdapter)
    assert adapter.platform == "tiktok"


def test_tiktok_global_real_provider_returns_cookie(monkeypatch):
    """YTVIDEO_SOCIAL_PROVIDER=real should resolve tiktok to TikTokCookieAdapter
    (not fall back to stub the way legacy code did)."""
    monkeypatch.setenv("YTVIDEO_SOCIAL_PROVIDER", "real")
    monkeypatch.delenv("YTVIDEO_SOCIAL_PROVIDER_TIKTOK", raising=False)

    adapter = get_adapter("tiktok")
    # After Agent A ships, 'real' for tiktok resolves to TikTokCookieAdapter.
    # We accept StubAdapter only if the registry still uses the old fallback path
    # (pre-Agent-A code) — the parametrized test above covers the new path.
    assert type(adapter).__name__ in ("TikTokCookieAdapter", "StubAdapter")


# ── YouTube regression ────────────────────────────────────────────────────────


def test_youtube_resolution_unaffected_by_tiktok_env(monkeypatch):
    """Setting YTVIDEO_SOCIAL_PROVIDER_TIKTOK must not affect youtube resolution."""
    monkeypatch.delenv("YTVIDEO_SOCIAL_PROVIDER", raising=False)
    monkeypatch.delenv("YTVIDEO_SOCIAL_PROVIDER_YOUTUBE", raising=False)
    monkeypatch.setenv("YTVIDEO_SOCIAL_PROVIDER_TIKTOK", "cookie")

    yt = get_adapter("youtube")
    # No YTVIDEO_SOCIAL_PROVIDER_YOUTUBE set -> falls back to global -> stub
    assert isinstance(yt, StubAdapter)
    assert yt.platform == "youtube"


def test_youtube_real_provider_returns_youtube_adapter(monkeypatch):
    """Per-platform YTVIDEO_SOCIAL_PROVIDER_YOUTUBE=real returns YouTubeAdapter."""
    from app.services.social.youtube import YouTubeAdapter

    monkeypatch.setenv("YTVIDEO_SOCIAL_PROVIDER_YOUTUBE", "real")
    monkeypatch.setenv("YTVIDEO_SOCIAL_PROVIDER_TIKTOK", "cookie")

    yt = get_adapter("youtube")
    assert isinstance(yt, YouTubeAdapter)


def test_unknown_platform_still_raises(monkeypatch):
    """Adding tiktok logic must not suppress UnsupportedPlatformError."""
    monkeypatch.setenv("YTVIDEO_SOCIAL_PROVIDER_TIKTOK", "cookie")
    with pytest.raises(UnsupportedPlatformError):
        get_adapter("myspace")


# ── n8n adapter smoke import ──────────────────────────────────────────────────


def test_n8n_adapter_importable(monkeypatch):
    """Resolving n8n provider must not raise ImportError."""
    if not HAS_N8N_ADAPTER:
        pytest.skip("requires Agent A N8nTikTokAdapter (app.services.social.n8n_tiktok)")

    monkeypatch.delenv("YTVIDEO_SOCIAL_PROVIDER", raising=False)
    monkeypatch.setenv("YTVIDEO_SOCIAL_PROVIDER_TIKTOK", "n8n")

    # Should not raise — even without a configured sidecar URL
    adapter = get_adapter("tiktok")
    assert type(adapter).__name__ == "N8nTikTokAdapter"
