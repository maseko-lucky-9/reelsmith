"""Contract tests for /social/* (W1.6)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipRecord, JobRecord, PublishJob
from app.db.session import get_session
from app.main import create_app
from app.services import token_vault
from app.settings import settings


@pytest.fixture(autouse=True)
def _vault_key(monkeypatch):
    monkeypatch.setattr(settings, "oauth_encrypt_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "social_provider", "stub")
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
        yield client, clip_id, str(tmp_path / "stubs"), factory

    await engine.dispose()


async def test_account_lifecycle(social_client):
    client, *_ = social_client

    # List empty.
    r = await client.get("/social/accounts")
    assert r.status_code == 200
    assert r.json() == []

    # Create.
    r = await client.post(
        "/social/accounts",
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
        "/social/accounts",
        json={"platform": "myspace", "account_handle": "x", "access_token": "y"},
    )
    assert bad.status_code == 422

    # Delete.
    r = await client.delete(f"/social/accounts/{aid}")
    assert r.status_code == 204
    r = await client.delete(f"/social/accounts/{aid}")
    assert r.status_code == 404


async def test_publish_immediate_runs_via_stub(social_client):
    client, clip_id, *_ = social_client

    acct = (await client.post(
        "/social/accounts",
        json={"platform": "youtube", "account_handle": "@me",
              "access_token": "ya29.tok"},
    )).json()

    r = await client.post(
        "/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"],
              "title": "T", "description": "D", "hashtags": ["a", "b"]},
    )
    assert r.status_code == 201
    pj = r.json()
    assert pj["status"] in ("queued", "published")  # background may have completed
    assert "schedule_at" not in pj  # dropped with FR-032

    # Poll once.
    r = await client.get(f"/social/publish/{pj['id']}")
    assert r.status_code == 200


async def test_publish_create_rejects_schedule_at(social_client):
    """Scheduled publishing was dropped (FR-032, T010).

    An old client that still sends ``schedule_at`` must get a 422, not an
    immediate publish of a post it meant to delay.
    """
    client, clip_id, *_ = social_client
    acct = (await client.post(
        "/social/accounts",
        json={"platform": "youtube", "account_handle": "@me", "access_token": "tok"},
    )).json()

    r = await client.post(
        "/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"],
              "schedule_at": "2030-01-01T12:00:00+00:00"},
    )

    assert r.status_code == 422
    assert any(e["loc"][-1] == "schedule_at" for e in r.json()["detail"])
    jobs = await client.get("/social/jobs")
    assert jobs.json() == []


async def test_publish_with_unknown_clip_404(social_client):
    client, *_ = social_client
    acct = (await client.post(
        "/social/accounts",
        json={"platform": "youtube", "account_handle": "@me",
              "access_token": "tok"},
    )).json()
    r = await client.post(
        "/social/publish",
        json={"clip_id": "missing", "social_account_id": acct["id"]},
    )
    assert r.status_code == 404


async def test_publish_with_unknown_account_404(social_client):
    client, clip_id, *_ = social_client
    r = await client.post(
        "/social/publish",
        json={"clip_id": clip_id, "social_account_id": "missing"},
    )
    assert r.status_code == 404


async def test_list_publish_for_clip(social_client):
    client, clip_id, *_ = social_client
    acct = (await client.post(
        "/social/accounts",
        json={"platform": "youtube", "account_handle": "@me", "access_token": "tok"},
    )).json()
    await client.post(
        "/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"]},
    )

    r = await client.get(f"/social/publish?clip_id={clip_id}")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["clip_id"] == clip_id


# ── T-05: GET /social/jobs ───────────────────────────────────────────────

async def _seed_legacy_pending(factory, clip_id: str, account_id: str,
                               hours_ahead: int = 1) -> str:
    """Insert a ``pending`` row as written before scheduling was dropped.

    The API can no longer create one (FR-032); the list endpoint must still
    return and filter such rows.
    """
    async with factory() as session:
        pj = PublishJob(
            clip_id=clip_id, social_account_id=account_id, status="pending",
            schedule_at=datetime.now(timezone.utc) + timedelta(hours=hours_ahead),
        )
        session.add(pj)
        await session.commit()
        return pj.id


async def test_list_jobs_empty_returns_200(social_client):
    """No jobs → 200 with empty list, not 404."""
    client, *_ = social_client
    r = await client.get("/social/jobs")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_jobs_response_shape(social_client):
    """Response items contain the T-05 required fields."""
    client, clip_id, *_ = social_client
    acct = (await client.post(
        "/social/accounts",
        json={"platform": "youtube", "account_handle": "@me", "access_token": "tok"},
    )).json()
    await client.post(
        "/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"]},
    )

    r = await client.get("/social/jobs")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    item = items[0]
    for field in ("id", "clip_id", "status", "posted_at",
                  "platform", "external_url", "error"):
        assert field in item, f"missing field: {field}"
    assert "schedule_at" not in item  # dropped with FR-032
    assert item["clip_id"] == clip_id
    assert item["platform"] == "youtube"


async def test_list_jobs_status_filter_single(social_client):
    """?status=pending returns only pending jobs."""
    client, clip_id, _, factory = social_client
    acct = (await client.post(
        "/social/accounts",
        json={"platform": "youtube", "account_handle": "@me", "access_token": "tok"},
    )).json()

    # Immediate publish → queued, then the stub runner advances it
    await client.post(
        "/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"]},
    )
    # Legacy scheduled row → pending
    await _seed_legacy_pending(factory, clip_id, acct["id"])

    r = await client.get("/social/jobs?status=pending")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    assert all(i["status"] == "pending" for i in items)


async def test_list_jobs_status_filter_multi_valued(social_client):
    """?status=pending&status=published returns jobs with either status.

    Two legacy scheduled (pending) rows are seeded. We then filter for two
    statuses and assert both IDs appear.
    """
    client, clip_id, _, factory = social_client
    acct = (await client.post(
        "/social/accounts",
        json={"platform": "youtube", "account_handle": "@me", "access_token": "tok"},
    )).json()

    pj_a = await _seed_legacy_pending(factory, clip_id, acct["id"], hours_ahead=1)
    pj_b = await _seed_legacy_pending(factory, clip_id, acct["id"], hours_ahead=2)

    # Multi-valued filter should return both
    r = await client.get("/social/jobs?status=pending&status=queued")
    assert r.status_code == 200
    items = r.json()
    ids = {i["id"] for i in items}
    assert pj_a in ids
    assert pj_b in ids
    assert all(i["status"] in ("pending", "queued") for i in items)


async def test_list_jobs_status_filter_no_match_returns_empty(social_client):
    """?status=failed returns [] when no failed jobs exist."""
    client, clip_id, *_ = social_client
    acct = (await client.post(
        "/social/accounts",
        json={"platform": "youtube", "account_handle": "@me", "access_token": "tok"},
    )).json()
    await client.post(
        "/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"]},
    )

    r = await client.get("/social/jobs?status=failed")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_jobs_clip_id_filter(social_client):
    """?clip_id=<id> restricts results to that clip."""
    client, clip_id, *_ = social_client
    acct = (await client.post(
        "/social/accounts",
        json={"platform": "youtube", "account_handle": "@me", "access_token": "tok"},
    )).json()
    await client.post(
        "/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"]},
    )

    r = await client.get(f"/social/jobs?clip_id={clip_id}")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    assert all(i["clip_id"] == clip_id for i in items)

    r = await client.get("/social/jobs?clip_id=nonexistent")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_jobs_no_status_filter_returns_all(social_client):
    """Absence of ?status returns all jobs regardless of status."""
    client, clip_id, _, factory = social_client
    acct = (await client.post(
        "/social/accounts",
        json={"platform": "youtube", "account_handle": "@me", "access_token": "tok"},
    )).json()

    await client.post(
        "/social/publish",
        json={"clip_id": clip_id, "social_account_id": acct["id"]},
    )
    await _seed_legacy_pending(factory, clip_id, acct["id"])

    r = await client.get("/social/jobs")
    assert r.status_code == 200
    # Both jobs (immediate + legacy pending) should be present
    assert len(r.json()) >= 2


# ── T-06: POST /social/tiktok/connect — 412 when no stable key ───────────


async def test_tiktok_connect_412_when_no_encrypt_key(monkeypatch):
    """POST /social/tiktok/connect returns 412 when YTVIDEO_OAUTH_ENCRYPT_KEY unset.

    The autouse _vault_key fixture sets the key for all tests in this module.
    This test explicitly removes it and resets the vault so has_stable_key()
    returns False, triggering the 412 security gate.
    """
    monkeypatch.setattr(settings, "oauth_encrypt_key", None)
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
                "/social/tiktok/connect",
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
