"""Contract tests for /api/clips/{id}/enhance-speech (W1.8)
and /api/clips/{id}/enhance-audio (T-07)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipRecord, JobRecord
from app.db.session import get_session
from app.main import create_app


@pytest.fixture
async def enhance_client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.settings.settings.audio_enhance_provider", "passthrough")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    src = tmp_path / "clip.mp4"
    src.write_bytes(b"data")

    async with factory() as session:
        job = JobRecord(youtube_url="https://example.com/v")
        session.add(job)
        await session.flush()
        rendered = ClipRecord(job_id=job.id, start=0, end=10, output_path=str(src))
        unrendered = ClipRecord(job_id=job.id, start=0, end=10)
        session.add(rendered)
        session.add(unrendered)
        await session.commit()
        rid, uid = rendered.id, unrendered.id

    async def _override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, rid, uid, src

    await engine.dispose()


async def test_enhance_passthrough_creates_enhanced_file(enhance_client):
    client, rid, _, src = enhance_client
    r = await client.post(f"/api/clips/{rid}/enhance-speech")
    assert r.status_code == 200
    body = r.json()
    assert body["clip_id"] == rid
    assert body["provider"] == "passthrough"
    enhanced = Path(body["output_path"])
    assert enhanced.is_file()
    assert ".enhanced" in enhanced.name


async def test_enhance_unrendered_clip_409(enhance_client):
    client, _, uid, _ = enhance_client
    r = await client.post(f"/api/clips/{uid}/enhance-speech")
    assert r.status_code == 409


async def test_enhance_missing_clip_404(enhance_client):
    client, *_ = enhance_client
    r = await client.post("/api/clips/missing/enhance-speech")
    assert r.status_code == 404


# ── T-07: enhance-audio endpoint ────────────────────────────────────────────


@pytest.mark.parametrize("provider", ["loudnorm", "rnnoise", "passthrough"])
async def test_enhance_audio_returns_202_with_job_id(enhance_client, provider):
    """POST /enhance-audio returns 202 + job_id for every valid provider."""
    client, rid, _, src = enhance_client

    captured: list[tuple] = []

    def _fake_invoke(argv):
        captured.append(tuple(argv))

    with patch("app.services.audio_enhance_service._invoke", side_effect=_fake_invoke):
        r = await client.post(
            f"/api/clips/{rid}/enhance-audio",
            json={"provider": provider},
        )

    assert r.status_code == 202, r.text
    body = r.json()
    assert "job_id" in body
    assert body["clip_id"] == rid
    assert body["provider"] == provider


async def test_enhance_audio_loudnorm_argv(enhance_client):
    """Patching _invoke lets us assert the exact ffmpeg argv for loudnorm."""
    client, rid, _, src = enhance_client

    captured: list[tuple] = []

    def _fake_invoke(argv):
        captured.append(tuple(argv))

    with patch("app.services.audio_enhance_service._invoke", side_effect=_fake_invoke):
        r = await client.post(
            f"/api/clips/{rid}/enhance-audio",
            json={"provider": "loudnorm"},
        )

    assert r.status_code == 202
    assert len(captured) == 1
    argv = captured[0]
    assert argv[0] == "ffmpeg"
    assert "loudnorm" in " ".join(argv)


async def test_enhance_audio_rnnoise_argv(enhance_client):
    """Patching _invoke lets us assert the exact ffmpeg argv for rnnoise."""
    client, rid, _, src = enhance_client

    captured: list[tuple] = []

    def _fake_invoke(argv):
        captured.append(tuple(argv))

    with patch("app.services.audio_enhance_service._invoke", side_effect=_fake_invoke):
        r = await client.post(
            f"/api/clips/{rid}/enhance-audio",
            json={"provider": "rnnoise"},
        )

    assert r.status_code == 202
    assert len(captured) == 1
    argv = captured[0]
    assert argv[0] == "ffmpeg"
    assert "arnndn" in " ".join(argv)


async def test_enhance_audio_passthrough_no_invoke(enhance_client):
    """passthrough provider copies file without calling _invoke."""
    client, rid, _, src = enhance_client

    captured: list[tuple] = []

    def _fake_invoke(argv):
        captured.append(tuple(argv))

    with patch("app.services.audio_enhance_service._invoke", side_effect=_fake_invoke):
        r = await client.post(
            f"/api/clips/{rid}/enhance-audio",
            json={"provider": "passthrough"},
        )

    assert r.status_code == 202
    # passthrough uses shutil.copyfile, never _invoke
    assert captured == []


async def test_enhance_audio_unknown_provider_422(enhance_client):
    """Unknown provider returns 422 with allowed values listed."""
    client, rid, _, _ = enhance_client
    r = await client.post(
        f"/api/clips/{rid}/enhance-audio",
        json={"provider": "unknown_provider"},
    )
    assert r.status_code == 422
    text = r.text
    # Pydantic validation error body should reference valid providers
    assert "loudnorm" in text or "rnnoise" in text or "passthrough" in text


async def test_enhance_audio_missing_clip_404(enhance_client):
    """Missing clip → 404."""
    client, *_ = enhance_client
    r = await client.post(
        "/api/clips/does-not-exist/enhance-audio",
        json={"provider": "passthrough"},
    )
    assert r.status_code == 404


async def test_enhance_audio_unrendered_clip_409(enhance_client):
    """Clip without output_path → 409 (mirrors xml_export convention)."""
    client, _, uid, _ = enhance_client
    r = await client.post(
        f"/api/clips/{uid}/enhance-audio",
        json={"provider": "passthrough"},
    )
    assert r.status_code == 409
