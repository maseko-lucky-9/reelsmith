"""Contract tests for /api/jobs/{id}/reprompt (W1.10)."""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import JobRecord
from app.db.session import get_session
from app.main import create_app


@pytest.fixture
async def reprompt_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        job = JobRecord(youtube_url="https://example.com/v", prompt="orig")
        session.add(job)
        await session.commit()
        jid = job.id

    async def _override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, jid

    await engine.dispose()


async def test_reprompt_with_named_range(reprompt_client):
    client, jid = reprompt_client
    r = await client.post(
        f"/api/jobs/{jid}/reprompt",
        json={"prompt": "new prompt", "length_range": "1-3m"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["prompt"] == "new prompt"
    opts = body["pipeline_options"]
    assert opts["target_length_min_seconds"] == 60
    assert opts["target_length_max_seconds"] == 180
    assert opts["segment_proposer"] is True
    assert opts["render"] is False
    assert opts["transcription"] is False
    assert body["status"] == "pending"


async def test_reprompt_with_explicit_seconds(reprompt_client):
    client, jid = reprompt_client
    r = await client.post(
        f"/api/jobs/{jid}/reprompt",
        json={"length_min_seconds": 30, "length_max_seconds": 90},
    )
    assert r.status_code == 200
    opts = r.json()["pipeline_options"]
    assert opts["target_length_min_seconds"] == 30
    assert opts["target_length_max_seconds"] == 90


async def test_reprompt_invalid_range_name_422(reprompt_client):
    client, jid = reprompt_client
    r = await client.post(
        f"/api/jobs/{jid}/reprompt",
        json={"length_range": "9-99h"},
    )
    assert r.status_code == 422


async def test_reprompt_min_greater_than_max_422(reprompt_client):
    client, jid = reprompt_client
    r = await client.post(
        f"/api/jobs/{jid}/reprompt",
        json={"length_min_seconds": 200, "length_max_seconds": 100},
    )
    assert r.status_code == 422


async def test_reprompt_unknown_job_404(reprompt_client):
    client, _ = reprompt_client
    r = await client.post("/api/jobs/missing/reprompt", json={"prompt": "x"})
    assert r.status_code == 404


async def test_reprompt_preserves_prompt_when_not_provided(reprompt_client):
    client, jid = reprompt_client
    r = await client.post(
        f"/api/jobs/{jid}/reprompt",
        json={"length_range": "0-1m"},
    )
    assert r.status_code == 200
    assert r.json()["prompt"] == "orig"
