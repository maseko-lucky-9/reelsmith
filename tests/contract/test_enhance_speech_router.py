"""Contract tests for /api/clips/{id}/enhance-speech (W1.8)."""
from __future__ import annotations

from pathlib import Path

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
