"""Unit tests for the W1.5 social adapter registry + stub + orchestrator."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipRecord, JobRecord, PublishJob, SocialAccount
from app.services import token_vault
from app.services.social import (
    PublishRequest,
    UnsupportedPlatformError,
    get_adapter,
    supported_platforms,
)
from app.services.social.stub import StubAdapter
from app.services.social.youtube import YouTubeAdapter
from app.services.social_publish_service import run_publish_job


# ── Registry ────────────────────────────────────────────────────────────────


def test_supported_platforms_is_five():
    assert supported_platforms() == ("youtube", "tiktok", "instagram", "linkedin", "x")


def test_get_adapter_unknown_raises():
    with pytest.raises(UnsupportedPlatformError):
        get_adapter("myspace")


def test_get_adapter_default_is_stub(monkeypatch):
    monkeypatch.delenv("YTVIDEO_SOCIAL_PROVIDER", raising=False)
    monkeypatch.delenv("YTVIDEO_SOCIAL_PROVIDER_YOUTUBE", raising=False)
    a = get_adapter("youtube")
    assert isinstance(a, StubAdapter)
    assert a.platform == "youtube"


def test_get_adapter_per_platform_override(monkeypatch):
    monkeypatch.setenv("YTVIDEO_SOCIAL_PROVIDER", "stub")
    monkeypatch.setenv("YTVIDEO_SOCIAL_PROVIDER_YOUTUBE", "real")
    yt = get_adapter("youtube")
    assert isinstance(yt, YouTubeAdapter)
    # Other platforms still stub.
    assert isinstance(get_adapter("tiktok"), StubAdapter)


def test_get_adapter_real_for_unimplemented_falls_back_to_stub(monkeypatch):
    """Unimplemented live adapters fall back to stub so dashboard keeps working."""
    monkeypatch.setenv("YTVIDEO_SOCIAL_PROVIDER", "real")
    a = get_adapter("tiktok")
    assert isinstance(a, StubAdapter)


# ── Stub adapter ────────────────────────────────────────────────────────────


async def test_stub_adapter_writes_descriptor(tmp_path):
    a = StubAdapter("youtube")
    req = PublishRequest(
        platform="youtube",
        account_handle="@me",
        clip_path="/tmp/whatever.mp4",
        title="Hi",
        description="desc",
        hashtags=("a", "b"),
        access_token="ignored-by-stub",
        stub_dir=str(tmp_path),
    )
    res = await a.publish(req)
    assert res.external_post_id.startswith("stub_youtube_")
    assert res.external_post_url == f"stub://youtube/{res.external_post_id}"
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    body = json.loads(files[0].read_text())
    assert body["title"] == "Hi"
    assert body["hashtags"] == ["a", "b"]


# ── YouTube adapter (transport-mocked) ──────────────────────────────────────


async def test_youtube_adapter_resumable_upload(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake-mp4-bytes")

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/videos") and request.method == "POST":
            return httpx.Response(
                200,
                headers={"location": "https://upload.example/resumable/xyz"},
                json={},
            )
        if request.method == "PUT":
            return httpx.Response(200, json={"id": "VID12345"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = YouTubeAdapter(http=client)
        req = PublishRequest(
            platform="youtube",
            account_handle="@me",
            clip_path=str(clip),
            title="Greatest hits",
            description="my best clips",
            hashtags=("shorts",),
            access_token="ya29.fake-bearer",
        )
        result = await adapter.publish(req)

    assert result.external_post_id == "VID12345"
    assert result.external_post_url == "https://www.youtube.com/watch?v=VID12345"
    auth = calls[0].headers.get("authorization")
    assert auth == "Bearer ya29.fake-bearer"


async def test_youtube_adapter_missing_token_raises(tmp_path):
    adapter = YouTubeAdapter()
    req = PublishRequest(
        platform="youtube",
        account_handle="@me",
        clip_path=str(tmp_path / "missing.mp4"),
        title="t",
        description="d",
        access_token="",
    )
    with pytest.raises(ValueError, match="access token"):
        await adapter.publish(req)


# ── Orchestrator (run_publish_job) ──────────────────────────────────────────


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory, output_path: str | None) -> str:
    token_vault.reset_for_tests()
    enc = token_vault.encrypt("fake-bearer")
    async with factory() as session:
        job = JobRecord(youtube_url="https://example.com/v")
        session.add(job)
        await session.flush()
        clip = ClipRecord(job_id=job.id, start=0, end=10, output_path=output_path,
                          title="t", summary="s")
        session.add(clip)
        acct = SocialAccount(
            platform="youtube",
            account_handle="@me",
            access_token_enc=enc,
        )
        session.add(acct)
        await session.flush()
        pj = PublishJob(
            clip_id=clip.id,
            social_account_id=acct.id,
            status="queued",
        )
        session.add(pj)
        await session.commit()
        return pj.id


async def test_run_publish_job_happy_path(factory, tmp_path, monkeypatch):
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"x")
    pj_id = await _seed(factory, output_path=str(output))

    async with factory() as session:
        pj = await run_publish_job(
            session, pj_id, stub_dir=str(tmp_path / "stubs")
        )
    assert pj.status == "published"
    assert pj.external_post_id.startswith("stub_youtube_")
    assert pj.attempts == 1
    assert pj.posted_at is not None


async def test_run_publish_job_clip_without_output_fails(factory, monkeypatch):
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    pj_id = await _seed(factory, output_path=None)

    async with factory() as session:
        pj = await run_publish_job(session, pj_id)
    assert pj.status == "failed"
    assert "no output_path" in (pj.error or "")


async def test_run_publish_job_idempotent_on_terminal(factory, monkeypatch, tmp_path):
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"x")
    pj_id = await _seed(factory, output_path=str(output))

    async with factory() as session:
        first = await run_publish_job(
            session, pj_id, stub_dir=str(tmp_path / "stubs")
        )
        first_id = first.external_post_id
        second = await run_publish_job(
            session, pj_id, stub_dir=str(tmp_path / "stubs")
        )
    assert second.external_post_id == first_id
    assert second.attempts == 1  # not re-incremented
