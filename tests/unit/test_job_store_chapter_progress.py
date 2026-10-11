"""JobState.chapters_total / chapters_done on both stores (T052).

The job page shows "3 of 10 chapters done" from these two counters, so the
SQL store must persist them, and concurrent chapters must not lose an
increment: ``increment_chapters_done`` is atomic, and ``update`` never writes
``chapters_done`` back from its (possibly stale) snapshot.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bus.job_store import InMemoryJobStore, JobNotFoundError, SqlJobStore
from app.db import models as _models  # noqa: F401 — registers tables on Base
from app.db.base import Base
from app.domain.models import JobState


@pytest.fixture(params=["memory", "sql"])
async def store(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[Any]:
    if request.param == "memory":
        yield InMemoryJobStore()
        return
    # A file database with the default pool, as in the app: each session gets
    # its own connection, so concurrent increments really are concurrent
    # transactions (a StaticPool would share one connection between them).
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sql_store = SqlJobStore()
    sql_store._factory = async_sessionmaker(engine, expire_on_commit=False)
    yield sql_store
    await engine.dispose()


async def _create(store: Any, job_id: str = "j1") -> None:
    await store.create(JobState(job_id=job_id, url="https://yt.test/a", download_path="/tmp"))


async def test_counters_default_to_zero(store):
    await _create(store)

    job = await store.get("j1")

    assert (job.chapters_total, job.chapters_done) == (0, 0)


async def test_total_set_by_update_survives_get(store):
    await _create(store)

    await store.update("j1", lambda s: setattr(s, "chapters_total", 7))

    assert (await store.get("j1")).chapters_total == 7


async def test_increment_returns_done_and_total(store):
    await _create(store)
    await store.update("j1", lambda s: setattr(s, "chapters_total", 3))

    assert await store.increment_chapters_done("j1") == (1, 3)
    assert await store.increment_chapters_done("j1") == (2, 3)
    job = await store.get("j1")
    assert (job.chapters_done, job.chapters_total) == (2, 3)


async def test_concurrent_increments_are_not_lost(store):
    await _create(store)
    await store.update("j1", lambda s: setattr(s, "chapters_total", 20))

    results = await asyncio.gather(*(store.increment_chapters_done("j1") for _ in range(20)))

    assert (await store.get("j1")).chapters_done == 20
    assert sorted(done for done, _ in results) == list(range(1, 21))


async def test_update_from_a_stale_snapshot_keeps_done(store):
    """An update whose mutator ran on a snapshot read while done was 0 must
    not write that 0 back over increments made since."""
    await _create(store)
    await store.update("j1", lambda s: setattr(s, "chapters_total", 3))
    stale = await store.get("j1")
    assert stale.chapters_done == 0
    await store.increment_chapters_done("j1")
    await store.increment_chapters_done("j1")

    def _stale_mutator(s: JobState) -> None:
        s.current_step = "render"
        s.chapters_done = stale.chapters_done

    await store.update("j1", _stale_mutator)

    assert (await store.get("j1")).chapters_done == 2


async def test_update_of_an_unrelated_field_keeps_done(store):
    await _create(store)
    await store.increment_chapters_done("j1")

    await store.update("j1", lambda s: setattr(s, "status", "running"))

    assert (await store.get("j1")).chapters_done == 1


async def test_increment_of_unknown_job_raises(store):
    with pytest.raises(JobNotFoundError):
        await store.increment_chapters_done("nope")
