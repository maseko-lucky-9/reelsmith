"""``upsert_clip`` runs async mutators as well as sync ones, on both stores.

Regression for FR-014: the like/dislike routes passed an ``async def``
mutator that both stores called without awaiting, so the toggle never ran.
"""

from __future__ import annotations

import warnings
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


async def _seed(store: Any) -> None:
    await store.create(
        JobState(job_id="job-1", url="https://yt.test/x", download_path="/tmp")
    )
    await store.upsert_clip(
        "job-1", "c1", lambda c: c.update({"title": "t", "start": 1.0})
    )


async def test_async_mutator_change_persists(store):
    await _seed(store)

    async def _like(c: dict[str, Any]) -> None:
        c["liked"] = True

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        await store.upsert_clip("job-1", "c1", _like)

    clip = await store.get_clip("c1")
    assert clip is not None
    assert clip["liked"] is True
    assert clip["title"] == "t"


async def test_sync_mutator_still_persists(store):
    await _seed(store)

    await store.upsert_clip("job-1", "c1", lambda c: c.update({"disliked": True}))

    clip = await store.get_clip("c1")
    assert clip is not None
    assert clip["disliked"] is True
    assert clip["start"] == 1.0
