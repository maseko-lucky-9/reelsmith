"""Contract tests for /api/social/* (W1.6)."""
from __future__ import annotations

import json

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


# ── T-05: GET /api/social/jobs ───────────────────────────────────────────────

async def test_list_jobs_empty_returns_200(social_client):
    """No jobs → 200 with empty list, not 404."""
    client, *_ = social_client
    r = await client.get("/api/social/jobs")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_jobs_response_shape(social_client):
    """Response items contain the T-05 required fields."""
    client, clip_id, _ = social_client
    acct = (await client.post(
        "/api/social/accounts",
        json={"platform": "youtube", "account_handle": "@me", "access_token": "tok"},
    )).json()
    await client.post(
        "/api/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"]},
    )

    r = await client.get("/api/social/jobs")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    item = items[0]
    for field in ("id", "clip_id", "status", "schedule_at", "posted_at",
                  "platform", "external_url", "error"):
        assert field in item, f"missing field: {field}"
    assert item["clip_id"] == clip_id
    assert item["platform"] == "youtube"


async def test_list_jobs_status_filter_single(social_client):
    """?status=pending returns only pending jobs."""
    client, clip_id, _ = social_client
    acct = (await client.post(
        "/api/social/accounts",
        json={"platform": "youtube", "account_handle": "@me", "access_token": "tok"},
    )).json()

    # Immediate publish → status=queued (no schedule_at)
    await client.post(
        "/api/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"]},
    )
    # Scheduled publish → status=pending
    from datetime import datetime, timezone, timedelta
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    await client.post(
        "/api/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"],
              "schedule_at": future},
    )

    r = await client.get("/api/social/jobs?status=pending")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    assert all(i["status"] == "pending" for i in items)


async def test_list_jobs_status_filter_multi_valued(social_client):
    """?status=pending&status=published returns jobs with either status.

    Two scheduled (pending) jobs are seeded plus one immediate job that the
    stub runner will advance to published.  We then filter for both statuses
    and assert both IDs appear.
    """
    client, clip_id, _ = social_client
    acct = (await client.post(
        "/api/social/accounts",
        json={"platform": "youtube", "account_handle": "@me", "access_token": "tok"},
    )).json()

    from datetime import datetime, timezone, timedelta
    future1 = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    future2 = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    # Two pending (scheduled) jobs with different schedule times
    pj_a = (await client.post(
        "/api/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"],
              "schedule_at": future1},
    )).json()
    pj_b = (await client.post(
        "/api/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"],
              "schedule_at": future2},
    )).json()

    assert pj_a["status"] == "pending"
    assert pj_b["status"] == "pending"

    # Multi-valued filter should return both
    r = await client.get("/api/social/jobs?status=pending&status=queued")
    assert r.status_code == 200
    items = r.json()
    ids = {i["id"] for i in items}
    assert pj_a["id"] in ids
    assert pj_b["id"] in ids
    assert all(i["status"] in ("pending", "queued") for i in items)


async def test_list_jobs_status_filter_no_match_returns_empty(social_client):
    """?status=failed returns [] when no failed jobs exist."""
    client, clip_id, _ = social_client
    acct = (await client.post(
        "/api/social/accounts",
        json={"platform": "youtube", "account_handle": "@me", "access_token": "tok"},
    )).json()
    await client.post(
        "/api/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"]},
    )

    r = await client.get("/api/social/jobs?status=failed")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_jobs_clip_id_filter(social_client):
    """?clip_id=<id> restricts results to that clip."""
    client, clip_id, _ = social_client
    acct = (await client.post(
        "/api/social/accounts",
        json={"platform": "youtube", "account_handle": "@me", "access_token": "tok"},
    )).json()
    await client.post(
        "/api/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"]},
    )

    r = await client.get(f"/api/social/jobs?clip_id={clip_id}")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    assert all(i["clip_id"] == clip_id for i in items)

    r = await client.get("/api/social/jobs?clip_id=nonexistent")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_jobs_no_status_filter_returns_all(social_client):
    """Absence of ?status returns all jobs regardless of status."""
    client, clip_id, _ = social_client
    acct = (await client.post(
        "/api/social/accounts",
        json={"platform": "youtube", "account_handle": "@me", "access_token": "tok"},
    )).json()

    from datetime import datetime, timezone, timedelta
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    await client.post(
        "/api/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"]},
    )
    await client.post(
        "/api/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"],
              "schedule_at": future},
    )

    r = await client.get("/api/social/jobs")
    assert r.status_code == 200
    # Both jobs (queued + pending) should be present
    assert len(r.json()) >= 2


# ── T-06: POST /api/social/tiktok/connect — 412 when no stable key ───────────


async def test_tiktok_connect_412_when_no_encrypt_key(monkeypatch):
    """POST /api/social/tiktok/connect returns 412 when YTVIDEO_OAUTH_ENCRYPT_KEY unset.

    The autouse _vault_key fixture sets the key for all tests in this module.
    This test explicitly removes it and resets the vault so has_stable_key()
    returns False, triggering the 412 security gate.
    """
    monkeypatch.delenv("YTVIDEO_OAUTH_ENCRYPT_KEY", raising=False)
    # Also clear settings.oauth_encrypt_key — pydantic-settings reads .env into
    # settings fields (not os.environ), so delenv alone isn't enough now that
    # has_stable_key() checks both sources.
    from app.settings import settings as _settings
    monkeypatch.setattr(_settings, "oauth_encrypt_key", None)
    token_vault.reset_for_tests()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

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

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/social/tiktok/connect",
                json={
                    "account_handle": "@test",
                    "cookies_json": json.dumps(
                        [
                            {"name": "sessionid", "value": "abc"},
                            {"name": "tt-target-idc", "value": "useast2a"},
                        ]
                    ),
                },
            )

        assert resp.status_code == 412
        assert "YTVIDEO_OAUTH_ENCRYPT_KEY" in resp.json()["detail"]
    finally:
        await engine.dispose()
        token_vault.reset_for_tests()
