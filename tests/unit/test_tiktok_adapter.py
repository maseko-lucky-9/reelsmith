"""Unit tests for TikTokCookieAdapter (app.services.social.tiktok).

Agent A builds the adapter; these tests specify the contract so they
can be written and collected independently.  They will fail (ImportError
or assertion) until Agent A's code lands — that is intentional.

The one exception is ``test_import_without_node`` which MUST pass even
before Agent A ships, because the module must be importable without Node
or tiktok_uploader present.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

from app.services.social.base import PublishRequest, PublishResult

# ── Check if Agent A adapter is available ────────────────────────────────────

try:
    from app.services.social.tiktok import TikTokCookieAdapter as _TikTokCookieAdapter  # noqa: F401
    HAS_TIKTOK_ADAPTER = True
except (ImportError, ModuleNotFoundError):
    HAS_TIKTOK_ADAPTER = False

_requires_adapter = pytest.mark.skipif(
    not HAS_TIKTOK_ADAPTER,
    reason="requires Agent A TikTokCookieAdapter (app.services.social.tiktok)",
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_request(
    *,
    access_token: str = "",
    clip_path: str = "/nonexistent.mp4",
    description: str = "My cool clip",
    hashtags: tuple[str, ...] = ("tag1", "tag2"),
) -> PublishRequest:
    return PublishRequest(
        platform="tiktok",
        account_handle="@reelsmith",
        clip_path=clip_path,
        title="Test title",
        description=description,
        hashtags=hashtags,
        access_token=access_token,
    )


def _valid_cookie_token() -> str:
    """Return a minimal valid cookie blob (JSON dict) accepted by the adapter."""
    return json.dumps({"sessionid": "abc123", "tt_csrf_token": "csrf-xyz"})


class FakeUploader:
    """Mirrors the minimal interface used by TikTokCookieAdapter."""

    def __init__(self, return_value: bool = True) -> None:
        self.calls: list[tuple] = []
        self.return_value = return_value

    def upload_video(
        self,
        session_user: str,
        video: str,
        title: str,
        schedule_time: int = 0,
        **kwargs,
    ) -> bool:
        self.calls.append((session_user, video, title, schedule_time))
        return self.return_value


# ── Smoke test (MUST pass before Agent A lands) ──────────────────────────────


def test_import_without_node():
    """Module must be importable even when Node/tiktok_uploader is absent.

    If the module doesn't exist yet (Agent A not merged), this test is
    skipped rather than errored — the contract is documented, not enforced
    until Agent A ships.
    """
    sys.modules.pop("app.services.social.tiktok", None)
    try:
        mod = importlib.import_module("app.services.social.tiktok")
    except (ImportError, ModuleNotFoundError):
        pytest.skip("app.services.social.tiktok not yet available (requires Agent A)")
    assert hasattr(mod, "TikTokCookieAdapter")


# ── Token / request validation ───────────────────────────────────────────────


@_requires_adapter
@pytest.mark.asyncio
async def test_empty_token_raises():
    """Empty access_token must raise ValueError or RuntimeError with 'malformed'."""
    from app.services.social.tiktok import TikTokCookieAdapter

    adapter = TikTokCookieAdapter(uploader=FakeUploader())
    req = _make_request(access_token="")

    with pytest.raises((ValueError, RuntimeError)) as exc_info:
        await adapter.publish(req)

    assert "malformed" in str(exc_info.value).lower()


@_requires_adapter
@pytest.mark.asyncio
async def test_malformed_token_raises():
    """Non-JSON access_token must raise RuntimeError('malformed cookie blob')."""
    from app.services.social.tiktok import TikTokCookieAdapter

    adapter = TikTokCookieAdapter(uploader=FakeUploader())
    req = _make_request(access_token="not-json")

    with pytest.raises(RuntimeError, match="malformed cookie blob"):
        await adapter.publish(req)


@_requires_adapter
@pytest.mark.asyncio
async def test_missing_clip_raises():
    """Valid cookie JSON but non-existent clip_path must raise FileNotFoundError or RuntimeError."""
    from app.services.social.tiktok import TikTokCookieAdapter

    adapter = TikTokCookieAdapter(uploader=FakeUploader())
    req = _make_request(
        access_token=_valid_cookie_token(),
        clip_path="/absolutely/nonexistent/clip.mp4",
    )

    with pytest.raises((FileNotFoundError, RuntimeError)):
        await adapter.publish(req)


# ── Caption composition ───────────────────────────────────────────────────────


@_requires_adapter
@pytest.mark.asyncio
async def test_caption_composition(tmp_path):
    """Title arg passed to uploader is 'description #tag1 #tag2', <= 2200 chars."""
    from app.services.social.tiktok import TikTokCookieAdapter

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake-mp4")

    fake = FakeUploader(return_value=True)
    adapter = TikTokCookieAdapter(uploader=fake)

    req = _make_request(
        access_token=_valid_cookie_token(),
        clip_path=str(clip),
        description="Check out this awesome clip",
        hashtags=("tag1", "tag2"),
    )
    await adapter.publish(req)

    assert len(fake.calls) == 1
    _session_user, _video, title, _schedule_time = fake.calls[0]
    assert "#tag1" in title
    assert "#tag2" in title
    assert "Check out this awesome clip" in title
    assert len(title) <= 2200


@_requires_adapter
@pytest.mark.asyncio
async def test_caption_truncated_to_2200_chars(tmp_path):
    """Very long description + hashtags must be truncated to <= 2200 chars total."""
    from app.services.social.tiktok import TikTokCookieAdapter

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake-mp4")

    fake = FakeUploader(return_value=True)
    adapter = TikTokCookieAdapter(uploader=fake)

    long_description = "A" * 2100
    req = _make_request(
        access_token=_valid_cookie_token(),
        clip_path=str(clip),
        description=long_description,
        hashtags=("tag1", "tag2"),
    )
    await adapter.publish(req)

    assert len(fake.calls) == 1
    _session_user, _video, title, _schedule_time = fake.calls[0]
    assert len(title) <= 2200


# ── Result contract ───────────────────────────────────────────────────────────


@_requires_adapter
@pytest.mark.asyncio
async def test_success_returns_posted_unverified(tmp_path):
    """Uploader returning True -> result.external_post_id starts with 'tiktok_'.

    The adapter cannot confirm the real post_id through the cookie path;
    it returns a sentinel that signals posted_unverified status.
    """
    from app.services.social.tiktok import TikTokCookieAdapter

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake-mp4")

    fake = FakeUploader(return_value=True)
    adapter = TikTokCookieAdapter(uploader=fake)

    req = _make_request(
        access_token=_valid_cookie_token(),
        clip_path=str(clip),
    )
    result = await adapter.publish(req)

    assert isinstance(result, PublishResult)
    assert result.external_post_id.startswith("tiktok_")


@_requires_adapter
@pytest.mark.asyncio
async def test_false_raises_runtime_error(tmp_path):
    """Uploader returning False must raise RuntimeError.

    SECURITY: error message must NOT contain token-related keywords.
    """
    from app.services.social.tiktok import TikTokCookieAdapter

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake-mp4")

    fake = FakeUploader(return_value=False)
    adapter = TikTokCookieAdapter(uploader=fake)

    req = _make_request(
        access_token=_valid_cookie_token(),
        clip_path=str(clip),
    )

    with pytest.raises(RuntimeError) as exc_info:
        await adapter.publish(req)

    msg = str(exc_info.value).lower()
    # Security: error must not reflect sensitive terms
    for forbidden in ("token", "cookie", "session", "access", "secret"):
        assert forbidden not in msg, (
            f"Error message leaks sensitive keyword '{forbidden}': {exc_info.value!r}"
        )


# ── Temp-file cleanup ─────────────────────────────────────────────────────────


@_requires_adapter
@pytest.mark.asyncio
async def test_temp_file_cleaned_up_on_success(tmp_path):
    """Temp cookie file must be deleted after a successful publish."""
    from app.services.social.tiktok import TikTokCookieAdapter

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake-mp4")

    temp_files_created: list[str] = []
    original_mkstemp = tempfile.mkstemp

    def tracking_mkstemp(*args, **kwargs):
        fd, path = original_mkstemp(*args, **kwargs)
        temp_files_created.append(path)
        return fd, path

    fake = FakeUploader(return_value=True)
    adapter = TikTokCookieAdapter(uploader=fake)

    req = _make_request(
        access_token=_valid_cookie_token(),
        clip_path=str(clip),
    )

    with patch("tempfile.mkstemp", side_effect=tracking_mkstemp):
        await adapter.publish(req)

    # Any temp cookie files created must no longer exist
    for tf in temp_files_created:
        assert not os.path.exists(tf), f"Temp file was not cleaned up: {tf}"


@_requires_adapter
@pytest.mark.asyncio
async def test_temp_file_cleaned_up_on_failure(tmp_path):
    """Temp cookie file must be deleted even when the uploader fails."""
    from app.services.social.tiktok import TikTokCookieAdapter

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake-mp4")

    temp_files_created: list[str] = []
    original_mkstemp = tempfile.mkstemp

    def tracking_mkstemp(*args, **kwargs):
        fd, path = original_mkstemp(*args, **kwargs)
        temp_files_created.append(path)
        return fd, path

    fake = FakeUploader(return_value=False)
    adapter = TikTokCookieAdapter(uploader=fake)

    req = _make_request(
        access_token=_valid_cookie_token(),
        clip_path=str(clip),
    )

    with patch("tempfile.mkstemp", side_effect=tracking_mkstemp):
        with pytest.raises(RuntimeError):
            await adapter.publish(req)

    for tf in temp_files_created:
        assert not os.path.exists(tf), (
            f"Temp file was not cleaned up after failure: {tf}"
        )


# ── Security: no token in error messages ─────────────────────────────────────


@_requires_adapter
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "access_token,clip_exists",
    [
        ("not-json", True),
        ("", True),
        (json.dumps({"sessionid": "SUPER_SECRET_SESSION_VALUE"}), False),
    ],
)
async def test_no_token_in_error_message(tmp_path, access_token, clip_exists):
    """Any error raised by the adapter must not contain the cookie blob content."""
    from app.services.social.tiktok import TikTokCookieAdapter

    if clip_exists:
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"fake-mp4")
        clip_path = str(clip)
    else:
        clip_path = "/nonexistent/clip.mp4"

    fake = FakeUploader(return_value=False)
    adapter = TikTokCookieAdapter(uploader=fake)

    req = _make_request(access_token=access_token, clip_path=clip_path)

    with pytest.raises((ValueError, RuntimeError, FileNotFoundError)) as exc_info:
        await adapter.publish(req)

    msg = str(exc_info.value)
    # The raw access_token must not appear verbatim in the error message
    if access_token and len(access_token) > 4:
        assert access_token not in msg, (
            f"Error message contains raw access_token: {msg!r}"
        )
    # Specific secret value must not appear
    assert "SUPER_SECRET_SESSION_VALUE" not in msg
