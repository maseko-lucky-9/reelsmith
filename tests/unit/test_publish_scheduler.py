"""Unit tests for the publish_jobs scheduler scaffold (W1.4).

Tests the pure-function ``promote_due_jobs`` against an in-memory
SQLite engine. The APScheduler wrapper is exercised lightly (start
+ shutdown idempotency); we don't test real interval firing here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipRecord, JobRecord, PublishJob, SocialAccount
from app.services.publish_scheduler import PublishScheduler, promote_due_jobs


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory, *, schedule_at: datetime | None, status: str = "pending") -> str:
    async with factory() as session:
        job = JobRecord(youtube_url="https://example.com/v")
        session.add(job)
        await session.flush()
        clip = ClipRecord(job_id=job.id, start=0, end=10)
        session.add(clip)
        acct = SocialAccount(
            platform="youtube",
            account_handle="@me",
            access_token_enc=Fernet.generate_key(),  # arbitrary blob
            owner_id="local",
        )
        session.add(acct)
        await session.flush()
        pj = PublishJob(
            clip_id=clip.id,
            social_account_id=acct.id,
            status=status,
            schedule_at=schedule_at,
        )
        session.add(pj)
        await session.commit()
        return pj.id


async def test_promote_due_jobs_flips_pending_to_queued(factory):
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    pj_id = await _seed(factory, schedule_at=past)

    async with factory() as session:
        promoted = await promote_due_jobs(session)
    assert promoted == [pj_id]

    async with factory() as session:
        pj = (await session.execute(select(PublishJob))).scalar_one()
        assert pj.status == "queued"


async def test_promote_due_jobs_skips_future(factory):
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    await _seed(factory, schedule_at=future)

    async with factory() as session:
        promoted = await promote_due_jobs(session)
    assert promoted == []


async def test_promote_due_jobs_skips_non_pending(factory):
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    await _seed(factory, schedule_at=past, status="published")

    async with factory() as session:
        promoted = await promote_due_jobs(session)
    assert promoted == []


async def test_promote_due_jobs_skips_no_schedule_at(factory):
    # Immediate (un-scheduled) jobs are not the scheduler's concern.
    await _seed(factory, schedule_at=None)

    async with factory() as session:
        promoted = await promote_due_jobs(session)
    assert promoted == []


async def test_scheduler_start_shutdown_idempotent(factory):
    sched = PublishScheduler(factory, poll_seconds=60)
    await sched.start()
    await sched.start()  # idempotent
    await sched.shutdown()
    await sched.shutdown()  # idempotent
