"""Contract tests for /api/social/* (W1.6)."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipRecord, JobRecord
from app.db.session import get_session
from app.main import create_app
from app.services import token_vault


@pytest.fixture(autouse=True)
def _vault_key(monkeypatch):
    monkeypatch.setenv("YTVIDEO_OAUTH_ENCRYPT_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("YTVIDEO_SOCIAL_PROVIDER", "stub")
    token_vault.reset_for_tests()
    yield
    token_vault.reset_for_tests()


@pytest.fixture
async def social_client(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    output = tmp_path / "clip.mp4"
    output.write_bytes(b"x")

    async with factory() as session:
        job = JobRecord(youtube_url="https://example.com/v")
        session.add(job)
        await session.flush()
        clip = ClipRecord(
            job_id=job.id, start=0, end=10, output_path=str(output),
            title="Hi", summary="desc",
        )
        session.add(clip)
        await session.commit()
        clip_id = clip.id

    async def _override():
        async with factory() as session:
            yield session

    from app.routers.social_publish import get_publish_runner
    from app.services.social_publish_service import run_publish_job

    async def _runner_override():
        async def _run(pj_id: str):
            async with factory() as session:
                await run_publish_job(session, pj_id)
        return _run

    app = create_app()
    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[get_publish_runner] = _runner_override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, clip_id, str(tmp_path / "stubs")

    await engine.dispose()


async def test_account_lifecycle(social_client):
    client, *_ = social_client

    # List empty.
    r = await client.get("/api/social/accounts")
    assert r.status_code == 200
    assert r.json() == []

    # Create.
    r = await client.post(
        "/api/social/accounts",
        json={
            "platform": "youtube",
            "account_handle": "@me",
            "access_token": "ya29.tok",
            "scopes": ["youtube.upload"],
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["platform"] == "youtube"
    assert "access_token" not in body  # never echoed
    aid = body["id"]

    # Reject unsupported platform.
    bad = await client.post(
        "/api/social/accounts",
        json={"platform": "myspace", "account_handle": "x", "access_token": "y"},
    )
    assert bad.status_code == 422

    # Delete.
    r = await client.delete(f"/api/social/accounts/{aid}")
    assert r.status_code == 204
    r = await client.delete(f"/api/social/accounts/{aid}")
    assert r.status_code == 404


async def test_publish_immediate_runs_via_stub(social_client):
    client, clip_id, _ = social_client

    acct = (await client.post(
        "/api/social/accounts",
        json={"platform": "youtube", "account_handle": "@me",
              "access_token": "ya29.tok"},
    )).json()

    r = await client.post(
        "/api/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"],
              "title": "T", "description": "D", "hashtags": ["a", "b"]},
    )
    assert r.status_code == 201
    pj = r.json()
    assert pj["status"] in ("queued", "published")  # background may have completed

    # Poll once.
    r = await client.get(f"/api/social/publish/{pj['id']}")
    assert r.status_code == 200


async def test_publish_with_unknown_clip_404(social_client):
    client, _, _ = social_client
    acct = (await client.post(
        "/api/social/accounts",
        json={"platform": "youtube", "account_handle": "@me",
              "access_token": "tok"},
    )).json()
    r = await client.post(
        "/api/social/publish",
        json={"clip_id": "missing", "social_account_id": acct["id"]},
    )
    assert r.status_code == 404


async def test_publish_with_unknown_account_404(social_client):
    client, clip_id, _ = social_client
    r = await client.post(
        "/api/social/publish",
        json={"clip_id": clip_id, "social_account_id": "missing"},
    )
    assert r.status_code == 404


async def test_list_publish_for_clip(social_client):
    client, clip_id, _ = social_client
    acct = (await client.post(
        "/api/social/accounts",
        json={"platform": "youtube", "account_handle": "@me", "access_token": "tok"},
    )).json()
    await client.post(
        "/api/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"]},
    )

    r = await client.get(f"/api/social/publish?clip_id={clip_id}")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["clip_id"] == clip_id
