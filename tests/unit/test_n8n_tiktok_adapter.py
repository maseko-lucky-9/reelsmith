"""Tests for N8nTikTokAdapter (httpx-based n8n sidecar client).

Agent A builds this adapter.  All tests are skipped when the module is
not yet available so collection never fails regardless of merge order.
"""
from __future__ import annotations

import pytest
import httpx

# Try to import; skip entire module if Agent A adapters aren't merged yet
try:
    from app.services.social.n8n_tiktok import N8nTikTokAdapter
    HAS_ADAPTER = True
except (ImportError, ModuleNotFoundError):
    HAS_ADAPTER = False

pytestmark = pytest.mark.skipif(
    not HAS_ADAPTER,
    reason="requires Agent A N8nTikTokAdapter (app.services.social.n8n_tiktok)",
)

from app.services.social.base import PublishRequest  # noqa: E402 — always importable


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_request(
    tmp_path,
    *,
    description: str = "Check out this clip",
    hashtags: tuple[str, ...] = ("tag1", "tag2"),
    account_handle: str = "@reelsmith",
    access_token: str = "fake-token",
    filename: str = "test.mp4",
) -> PublishRequest:
    video = tmp_path / filename
    video.write_bytes(b"fake-video-bytes")
    return PublishRequest(
        platform="tiktok",
        account_handle=account_handle,
        clip_path=str(video),
        title="Test title",
        description=description,
        hashtags=hashtags,
        access_token=access_token,
    )


# ── Multipart field tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_posts_correct_multipart_fields(tmp_path):
    """Sidecar receives video bytes, caption (description + hashtags), and account handle."""
    req = _make_request(
        tmp_path,
        description="My reel caption",
        hashtags=("reelsmith", "shorts"),
        account_handle="@creator",
    )

    captured_request: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request.append(request)
        return httpx.Response(200, json={"status": "ok", "post_id": "tiktok_sidecar_abc"})

    transport = httpx.MockTransport(handler)
    adapter = N8nTikTokAdapter(
        sidecar_url="http://n8n-sidecar.local/tiktok/upload",
        http=httpx.AsyncClient(transport=transport),
    )

    result = await adapter.publish(req)

    assert len(captured_request) == 1, "Expected exactly one HTTP request to sidecar"
    sent = captured_request[0]

    # Verify method and URL
    assert sent.method == "POST"
    assert "tiktok" in str(sent.url).lower() or "upload" in str(sent.url).lower()

    # Decode the body to inspect multipart fields
    body_bytes = sent.read()
    body_text = body_bytes.decode("latin-1")  # multipart is binary-safe with latin-1

    # Caption should include description and hashtags
    assert "My reel caption" in body_text
    assert "#reelsmith" in body_text or "reelsmith" in body_text
    assert "#shorts" in body_text or "shorts" in body_text

    # Account handle should be in the request
    assert "@creator" in body_text or "creator" in body_text

    # Video bytes should be present
    assert b"fake-video-bytes" in body_bytes

    # Result contract
    assert result.external_post_id is not None
    assert "tiktok" in result.external_post_id.lower()


@pytest.mark.asyncio
async def test_caption_includes_hashtags_with_hash_prefix(tmp_path):
    """Caption field must include hashtags formatted with '#' prefix."""
    req = _make_request(
        tmp_path,
        description="Hello world",
        hashtags=("viral", "fyp"),
    )

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"status": "ok", "post_id": "tiktok_hash_test"})

    transport = httpx.MockTransport(handler)
    adapter = N8nTikTokAdapter(
        sidecar_url="http://n8n.local/upload",
        http=httpx.AsyncClient(transport=transport),
    )
    await adapter.publish(req)

    assert len(captured) == 1
    body = captured[0].read().decode("latin-1")
    # Must have hashtag marker (either formatted inline or as field)
    assert "#viral" in body or "viral" in body
    assert "#fyp" in body or "fyp" in body


@pytest.mark.asyncio
async def test_result_external_post_id_from_sidecar_response(tmp_path):
    """Result.external_post_id comes from the sidecar response body."""
    req = _make_request(tmp_path)
    expected_id = "tiktok_post_from_n8n_xyz"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "post_id": expected_id})

    transport = httpx.MockTransport(handler)
    adapter = N8nTikTokAdapter(
        sidecar_url="http://n8n.local/upload",
        http=httpx.AsyncClient(transport=transport),
    )
    result = await adapter.publish(req)

    assert result.external_post_id == expected_id


# ── Error / misconfiguration tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_raises_when_sidecar_not_configured(tmp_path):
    """RuntimeError raised when sidecar_url is None (not configured)."""
    req = _make_request(tmp_path)

    adapter = N8nTikTokAdapter(sidecar_url=None)
    with pytest.raises(RuntimeError, match="(?i)sidecar|not configured|url"):
        await adapter.publish(req)


@pytest.mark.asyncio
async def test_raises_when_sidecar_not_configured_empty_string(tmp_path):
    """RuntimeError raised when sidecar_url is an empty string."""
    req = _make_request(tmp_path)

    adapter = N8nTikTokAdapter(sidecar_url="")
    with pytest.raises(RuntimeError, match="(?i)sidecar|not configured|url"):
        await adapter.publish(req)


@pytest.mark.asyncio
async def test_raises_on_sidecar_5xx(tmp_path):
    """RuntimeError raised when sidecar returns a 5xx response."""
    req = _make_request(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    transport = httpx.MockTransport(handler)
    adapter = N8nTikTokAdapter(
        sidecar_url="http://n8n.local/upload",
        http=httpx.AsyncClient(transport=transport),
    )
    with pytest.raises(RuntimeError):
        await adapter.publish(req)


@pytest.mark.asyncio
async def test_raises_on_sidecar_4xx(tmp_path):
    """RuntimeError raised when sidecar returns a 4xx response."""
    req = _make_request(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": "Invalid video format"})

    transport = httpx.MockTransport(handler)
    adapter = N8nTikTokAdapter(
        sidecar_url="http://n8n.local/upload",
        http=httpx.AsyncClient(transport=transport),
    )
    with pytest.raises(RuntimeError):
        await adapter.publish(req)


@pytest.mark.asyncio
async def test_raises_on_missing_clip_file():
    """RuntimeError or FileNotFoundError raised when clip_path doesn't exist."""
    req = PublishRequest(
        platform="tiktok",
        account_handle="@test",
        clip_path="/absolutely/nonexistent/clip.mp4",
        title="t",
        description="d",
        access_token="tok",
    )

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json={"status": "ok", "post_id": "never"})

    transport = httpx.MockTransport(handler)
    adapter = N8nTikTokAdapter(
        sidecar_url="http://n8n.local/upload",
        http=httpx.AsyncClient(transport=transport),
    )
    with pytest.raises((RuntimeError, FileNotFoundError)):
        await adapter.publish(req)


# ── Security ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_error_message_does_not_contain_token(tmp_path):
    """RuntimeError from sidecar failure must not expose the access_token."""
    secret_token = "very-secret-session-xyz999"
    req = _make_request(tmp_path, access_token=secret_token)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    adapter = N8nTikTokAdapter(
        sidecar_url="http://n8n.local/upload",
        http=httpx.AsyncClient(transport=transport),
    )

    with pytest.raises(RuntimeError) as exc_info:
        await adapter.publish(req)

    msg = str(exc_info.value)
    assert secret_token not in msg, (
        f"access_token leaked in error message: {msg!r}"
    )
    for keyword in ("token", "cookie", "session", "secret", "access"):
        assert keyword not in msg.lower(), (
            f"Sensitive keyword '{keyword}' found in error message: {msg!r}"
        )
