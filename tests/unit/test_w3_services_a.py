"""Unit tests for W3.3 + W3.4 services.

The W3.2 ``scheduler_service`` tests went with the service (T045):
scheduled publishing was dropped (FR-032).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import (
    ClipRecord,
    JobRecord,
    PublishJob,
    ShareLink,
    SocialAccount,
)
from app.services import (
    analytics_service as anal,
)
from app.services import (
    share_link_service as sl,
)
from app.settings import settings


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
    monkeypatch.setattr(settings, "share_link_secret", "test-secret-123")
    _, clip_id, _ = await _seed_publish_job(factory)

    async with factory() as session:
        link = await sl.create_link(session, clip_id, ttl_hours=1)

    assert link.token.startswith("rs.")
    assert sl.verify_token(link.token) == clip_id


async def test_share_link_expired(monkeypatch):
    monkeypatch.setattr(settings, "share_link_secret", "x")
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    token = sl._build_token("clip-1", past, "x")
    assert sl.verify_token(token) is None


async def test_share_link_tampered_signature(monkeypatch):
    monkeypatch.setattr(settings, "share_link_secret", "x")
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    token = sl._build_token("clip-1", future, "x")
    bad = token[:-3] + "AAA"
    assert sl.verify_token(bad) is None


async def test_share_link_wrong_prefix(monkeypatch):
    monkeypatch.setattr(settings, "share_link_secret", "x")
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    token = sl._build_token("clip-1", future, "x").replace("rs.", "xx.", 1)
    assert sl.verify_token(token) is None


async def test_share_link_revoke(factory, monkeypatch):
    monkeypatch.setattr(settings, "share_link_secret", "x")
    _, clip_id, _ = await _seed_publish_job(factory)
    async with factory() as session:
        link = await sl.create_link(session, clip_id)

    async with factory() as session:
        ok = await sl.revoke(session, link.token)
    assert ok is True

    async with factory() as session:
        assert await sl.is_revoked(session, link.token) is True
