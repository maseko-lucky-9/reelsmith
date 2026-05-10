"""Contract tests for /api/clips/{id}/ai-hook (W1.7)."""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipRecord, JobRecord
from app.db.session import get_session
from app.main import create_app
from app.services import ai_hook_service


@pytest.fixture
async def hook_client(monkeypatch):
    from app.routers import ai_hook as ai_hook_router
    monkeypatch.setattr(
        ai_hook_service, "generate_hook",
        lambda transcript, **kwargs: "Generated hook.",
    )
    monkeypatch.setattr(
        ai_hook_router, "generate_hook",
        lambda transcript, **kwargs: "Generated hook.",
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        job = JobRecord(youtube_url="https://example.com/v")
        session.add(job)
        await session.flush()
        clip = ClipRecord(job_id=job.id, start=0, end=10,
                          transcript={"text": "long transcript text"})
        session.add(clip)
        await session.commit()
        cid = clip.id

    async def _override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, cid

    await engine.dispose()


async def test_ai_hook_persists_on_clip(hook_client):
    client, cid = hook_client
    r = await client.post(f"/api/clips/{cid}/ai-hook")
    assert r.status_code == 200
    assert r.json() == {"clip_id": cid, "hook": "Generated hook."}

    # Hit it again — same clip, same shape.
    r2 = await client.post(f"/api/clips/{cid}/ai-hook")
    assert r2.json()["hook"] == "Generated hook."


async def test_ai_hook_unknown_clip_404(hook_client):
    client, _ = hook_client
    r = await client.post("/api/clips/missing/ai-hook")
    assert r.status_code == 404
