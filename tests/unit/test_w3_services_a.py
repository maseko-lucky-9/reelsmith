"""Unit tests for W3.2 + W3.3 + W3.4 services."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import (
    ClipRecord,
    JobRecord,
    PublishJob,
    ScheduledPost,
    ShareLink,
    SocialAccount,
)
from app.services import (
    analytics_service as anal,
    scheduler_service as sch,
    share_link_service as sl,
)


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed_publish_job(factory) -> tuple[str, str, str]:
    async with factory() as session:
        job = JobRecord(youtube_url="https://x.test")
        session.add(job)
        await session.flush()
        clip = ClipRecord(job_id=job.id, start=0, end=5)
        session.add(clip)
        acct = SocialAccount(platform="youtube", account_handle="@me",
                             access_token_enc=Fernet.generate_key())
        session.add(acct)
        await session.flush()
        pj = PublishJob(clip_id=clip.id, social_account_id=acct.id, status="pending")
        session.add(pj)
        await session.commit()
        return pj.id, clip.id, acct.id


# ── W3.2 scheduler_service ──────────────────────────────────────────────


async def test_claim_due_posts_flips_status(factory):
    pj_id, _, _ = await _seed_publish_job(factory)
    past = datetime.now(timezone.utc) - timedelta(seconds=30)
    async with factory() as session:
        sp = ScheduledPost(publish_job_id=pj_id, scheduled_for=past)
        session.add(sp)
        await session.commit()

    async with factory() as session:
        claimed = await sch.claim_due_posts(session, worker_id="w1")
    assert len(claimed) == 1

    async with factory() as session:
        sp = (await session.execute(select(ScheduledPost))).scalar_one()
        assert sp.status == "posting"
        assert sp.worker_id == "w1"
        assert sp.locked_at is not None


async def test_claim_due_posts_skips_future(factory):
    pj_id, _, _ = await _seed_publish_job(factory)
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    async with factory() as session:
        session.add(ScheduledPost(publish_job_id=pj_id, scheduled_for=future))
        await session.commit()

    async with factory() as session:
        claimed = await sch.claim_due_posts(session, worker_id="w1")
    assert claimed == []


async def test_mark_published(factory):
    pj_id, _, _ = await _seed_publish_job(factory)
    past = datetime.now(timezone.utc) - timedelta(seconds=30)
    async with factory() as session:
        sp = ScheduledPost(publish_job_id=pj_id, scheduled_for=past)
        session.add(sp)
        await session.commit()
        sp_id = sp.id

    async with factory() as session:
        await sch.mark_published(session, sp_id)

    async with factory() as session:
        sp = (await session.execute(select(ScheduledPost))).scalar_one()
        assert sp.status == "published"
        assert sp.attempts == 1


async def test_mark_published_with_error(factory):
    pj_id, _, _ = await _seed_publish_job(factory)
    past = datetime.now(timezone.utc) - timedelta(seconds=30)
    async with factory() as session:
        sp = ScheduledPost(publish_job_id=pj_id, scheduled_for=past)
        session.add(sp)
        await session.commit()
        sp_id = sp.id

    async with factory() as session:
        await sch.mark_published(session, sp_id, error="upstream 502")

    async with factory() as session:
        sp = (await session.execute(select(ScheduledPost))).scalar_one()
        assert sp.status == "failed"
        assert sp.last_error == "upstream 502"


# ── W3.3 analytics_service ─────────────────────────────────────────────


async def test_analytics_record_and_aggregate(factory):
    _, clip_id, _ = await _seed_publish_job(factory)

    async with factory() as session:
        await anal.record_snapshot(
            session, clip_id=clip_id, platform="youtube",
            external_post_id="VID1",
            metrics=anal.AnalyticsRecord(impressions=100, views=50,
                                         watch_time_seconds=300, likes=8),
        )
        await anal.record_snapshot(
            session, clip_id=clip_id, platform="youtube",
            external_post_id="VID1",
            metrics=anal.AnalyticsRecord(impressions=150, views=80,
                                         watch_time_seconds=500, likes=12),
        )
        await anal.record_snapshot(
            session, clip_id=clip_id, platform="tiktok",
            external_post_id="TT1",
            metrics=anal.AnalyticsRecord(impressions=200, views=150,
                                         watch_time_seconds=400, likes=20),
        )

    async with factory() as session:
        latest = await anal.latest_per_platform(session, clip_id)
        assert set(latest.keys()) == {"youtube", "tiktok"}
        # YouTube latest is the second record (impressions=150).
        assert latest["youtube"].impressions == 150

        agg = await anal.aggregate_for_clip(session, clip_id)
        assert agg.impressions == 150 + 200
        assert agg.views == 80 + 150
        assert agg.likes == 12 + 20


async def test_analytics_aggregate_empty(factory):
    _, clip_id, _ = await _seed_publish_job(factory)
    async with factory() as session:
        agg = await anal.aggregate_for_clip(session, clip_id)
    assert agg.impressions == 0
    assert agg.likes == 0


# ── W3.4 share_link_service ────────────────────────────────────────────


async def test_share_link_round_trip(factory, monkeypatch):
    monkeypatch.setenv("YTVIDEO_SHARE_LINK_SECRET", "test-secret-123")
    _, clip_id, _ = await _seed_publish_job(factory)

    async with factory() as session:
        link = await sl.create_link(session, clip_id, ttl_hours=1)

    assert link.token.startswith("rs.")
    assert sl.verify_token(link.token) == clip_id


async def test_share_link_expired(monkeypatch):
    monkeypatch.setenv("YTVIDEO_SHARE_LINK_SECRET", "x")
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    token = sl._build_token("clip-1", past, "x")
    assert sl.verify_token(token) is None


async def test_share_link_tampered_signature(monkeypatch):
    monkeypatch.setenv("YTVIDEO_SHARE_LINK_SECRET", "x")
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    token = sl._build_token("clip-1", future, "x")
    bad = token[:-3] + "AAA"
    assert sl.verify_token(bad) is None


async def test_share_link_wrong_prefix(monkeypatch):
    monkeypatch.setenv("YTVIDEO_SHARE_LINK_SECRET", "x")
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    token = sl._build_token("clip-1", future, "x").replace("rs.", "xx.", 1)
    assert sl.verify_token(token) is None


async def test_share_link_revoke(factory, monkeypatch):
    monkeypatch.setenv("YTVIDEO_SHARE_LINK_SECRET", "x")
    _, clip_id, _ = await _seed_publish_job(factory)
    async with factory() as session:
        link = await sl.create_link(session, clip_id)

    async with factory() as session:
        ok = await sl.revoke(session, link.token)
    assert ok is True

    async with factory() as session:
        assert await sl.is_revoked(session, link.token) is True
