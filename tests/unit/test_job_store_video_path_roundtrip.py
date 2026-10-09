"""JobState.video_path survives a store round trip on both stores.

Re-rendering a single clip (FR-015) reads the job's downloaded source video
back from the store, so the SQL store must persist it (``jobs.video_path``).
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


async def test_video_path_set_by_update_survives_get(store):
    await store.create(
        JobState(job_id="j1", url="https://yt.test/a", download_path="/tmp")
    )

    await store.update("j1", lambda s: setattr(s, "video_path", "/videos/a.mp4"))

    assert (await store.get("j1")).video_path == "/videos/a.mp4"


async def test_video_path_given_at_create_survives_get(store):
    await store.create(
        JobState(
            job_id="j2",
            url="upload:///tmp/b.mp4",
            download_path="/tmp",
            video_path="/videos/b.mp4",
        )
    )

    assert (await store.get("j2")).video_path == "/videos/b.mp4"


async def test_video_path_kept_by_unrelated_update(store):
    await store.create(
        JobState(job_id="j3", url="https://yt.test/c", download_path="/tmp")
    )
    await store.update("j3", lambda s: setattr(s, "video_path", "/videos/c.mp4"))

    await store.update("j3", lambda s: setattr(s, "status", "completed"))

    fetched = await store.get("j3")
    assert fetched.video_path == "/videos/c.mp4"
    assert fetched.status == "completed"


async def test_video_path_defaults_none(store):
    await store.create(
        JobState(job_id="j4", url="https://yt.test/d", download_path="/tmp")
    )

    assert (await store.get("j4")).video_path is None
