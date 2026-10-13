"""JobState.title and .duration survive a store round trip on both stores.

The orchestrator sets them after the download. The SQL store only copied a
fixed list of fields back to the row, so with the default ``sql`` store they
were always ``None`` on the next read and the job page showed "Loading title…"
forever (``jobs.title`` / ``jobs.duration``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bus.job_store import InMemoryJobStore, SqlJobStore
from app.db import models as _models  # noqa: F401 — registers tables on Base
from app.db.base import Base
from app.domain.models import JobState


@pytest.fixture(params=["memory", "sql"])
async def store(request: pytest.FixtureRequest) -> AsyncIterator[Any]:
    if request.param == "memory":
        yield InMemoryJobStore()
        return
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sql_store = SqlJobStore()
    sql_store._factory = async_sessionmaker(engine, expire_on_commit=False)
    yield sql_store
    await engine.dispose()


def _job(job_id: str, **extra: Any) -> JobState:
    return JobState(job_id=job_id, url=f"https://yt.test/{job_id}", download_path="/tmp", **extra)


async def test_title_and_duration_set_by_update_survive_get(store):
    await store.create(_job("j1"))

    def _downloaded(s: JobState) -> None:
        s.title = "My video"
        s.duration = 104.5

    await store.update("j1", _downloaded)

    fetched = await store.get("j1")
    assert fetched.title == "My video"
    assert fetched.duration == 104.5


async def test_title_and_duration_given_at_create_survive_get(store):
    await store.create(_job("j2", title="Uploaded", duration=12.0))

    fetched = await store.get("j2")
    assert fetched.title == "Uploaded"
    assert fetched.duration == 12.0


async def test_title_and_duration_kept_by_unrelated_update(store):
    await store.create(_job("j3"))
    await store.update("j3", lambda s: (setattr(s, "title", "Kept"), setattr(s, "duration", 7.0)))

    await store.update("j3", lambda s: setattr(s, "status", "completed"))

    fetched = await store.get("j3")
    assert (fetched.title, fetched.duration, fetched.status) == ("Kept", 7.0, "completed")


async def test_title_and_duration_default_none(store):
    await store.create(_job("j4"))

    fetched = await store.get("j4")
    assert fetched.title is None
    assert fetched.duration is None


async def test_title_and_duration_are_listed_with_the_job(store):
    await store.create(_job("j5"))
    await store.update("j5", lambda s: (setattr(s, "title", "Listed"), setattr(s, "duration", 3.0)))

    listed = {j.job_id: j for j in await store.list_jobs()}

    assert (listed["j5"].title, listed["j5"].duration) == ("Listed", 3.0)
