"""Smoke + relationship tests for the W3.1 ORM models."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import (
    ApiToken,
    ClipAnalyticsSnapshot,
    ClipRecord,
    JobRecord,
    PublishJob,
    ScheduledPost,
    ShareLink,
    SocialAccount,
    Webhook,
    Workspace,
    WorkspaceMember,
)


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_workspace_round_trip(factory):
    async with factory() as session:
        ws = Workspace(name="Acme")
        session.add(ws)
        await session.commit()

    async with factory() as session:
        rows = (await session.execute(select(Workspace))).scalars().all()
        assert any(w.name == "Acme" for w in rows)


async def test_workspace_member_unique_constraint_present():
    """Schema-level: (workspace_id, user_id) must be unique."""
    constraints = WorkspaceMember.__table__.constraints
    names = {c.name for c in constraints if c.name}
    assert "uq_wm_workspace_user" in names


async def test_scheduled_post_round_trip(factory):
    async with factory() as session:
        job = JobRecord(youtube_url="https://x.test")
        session.add(job)
        await session.flush()
        clip = ClipRecord(job_id=job.id, start=0, end=5)
        session.add(clip)
        from cryptography.fernet import Fernet
        acct = SocialAccount(platform="youtube", account_handle="@me",
                             access_token_enc=Fernet.generate_key())
        session.add(acct)
        await session.flush()
        pj = PublishJob(clip_id=clip.id, social_account_id=acct.id, status="pending")
        session.add(pj)
        await session.flush()
        from datetime import datetime, timedelta, timezone
        sp = ScheduledPost(
            publish_job_id=pj.id,
            scheduled_for=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        session.add(sp)
        await session.commit()
        assert sp.status == "scheduled"
        assert sp.attempts == 0


async def test_share_link_unique_token(factory):
    async with factory() as session:
        job = JobRecord(youtube_url="https://x.test")
        session.add(job)
        await session.flush()
        clip = ClipRecord(job_id=job.id, start=0, end=5)
        session.add(clip)
        await session.flush()
        session.add(ShareLink(clip_id=clip.id, token="tok-abc"))
        await session.commit()

    async with factory() as session:
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            session.add(ShareLink(clip_id=clip.id, token="tok-abc"))
            await session.commit()


async def test_webhook_round_trip(factory):
    async with factory() as session:
        wh = Webhook(
            url="https://hooks.test/abc",
            events=["clip.published"],
            secret_enc=b"\x00\x01\x02",
        )
        session.add(wh)
        await session.commit()
        assert wh.active is True


async def test_api_token_round_trip(factory):
    async with factory() as session:
        session.add(Workspace(id="ws1", name="Workspace 1"))
        await session.flush()
        tok = ApiToken(
            name="ci-token",
            token_hash="hashed-bytes",
            token_prefix="rs_",
            workspace_id="ws1",
        )
        session.add(tok)
        await session.commit()
        assert tok.revoked is False


async def test_clip_analytics_snapshot_round_trip(factory):
    async with factory() as session:
        job = JobRecord(youtube_url="https://x.test")
        session.add(job)
        await session.flush()
        clip = ClipRecord(job_id=job.id, start=0, end=5)
        session.add(clip)
        await session.flush()
        snap = ClipAnalyticsSnapshot(
            clip_id=clip.id,
            platform="youtube",
            external_post_id="VID12345",
            impressions=100, views=50, watch_time_seconds=300,
            likes=8, comments=2, shares=1,
        )
        session.add(snap)
        await session.commit()
