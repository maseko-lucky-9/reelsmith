"""Targeted JobStore lookups, run against both stores.

``SqlJobStore`` runs on an in-memory aiosqlite database here (fast, default
suite); ``tests/integration/test_sql_job_store.py`` covers it on Postgres.
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

ACTIVE = ("completed", "running", "pending")


@pytest.fixture(params=["memory", "sql"])
async def store(request: pytest.FixtureRequest) -> AsyncIterator[Any]:
    if request.param == "memory":
        yield InMemoryJobStore()
        return
    # One shared connection: each new :memory: connection is an empty DB.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sql_store = SqlJobStore()
    sql_store._factory = async_sessionmaker(engine, expire_on_commit=False)
    yield sql_store
    await engine.dispose()


async def _job(store: Any, job_id: str, url: str, status: str = "pending") -> None:
    await store.create(JobState(job_id=job_id, url=url, download_path="/tmp"))
    if status != "pending":
        await store.update(job_id, lambda s: setattr(s, "status", status))


# ── find_job_by_url ───────────────────────────────────────────────────────────


async def test_find_job_by_url_returns_exact_match(store):
    await _job(store, "a", "https://yt.test/watch?v=abc", "completed")

    found = await store.find_job_by_url("https://yt.test/watch?v=abc", ACTIVE)

    assert found is not None
    assert found.job_id == "a"
    assert found.status == "completed"


async def test_find_job_by_url_ignores_substring_and_prefix_matches(store):
    await _job(store, "long", "https://yt.test/watch?v=abc&t=10", "completed")
    await _job(store, "upper", "HTTPS://YT.TEST/WATCH?V=ABC", "completed")

    assert await store.find_job_by_url("https://yt.test/watch?v=abc", ACTIVE) is None
    assert await store.find_job_by_url("yt.test/watch?v=abc", ACTIVE) is None


async def test_find_job_by_url_skips_statuses_not_asked_for(store):
    await _job(store, "failed", "https://yt.test/x", "failed")

    assert await store.find_job_by_url("https://yt.test/x", ACTIVE) is None
    found = await store.find_job_by_url("https://yt.test/x", ("failed",))
    assert found is not None and found.job_id == "failed"


async def test_find_job_by_url_prefers_newest_match(store):
    await _job(store, "old", "https://yt.test/x", "completed")
    await _job(store, "new", "https://yt.test/x", "running")

    found = await store.find_job_by_url("https://yt.test/x", ACTIVE)

    assert found is not None and found.job_id == "new"


async def test_find_job_by_url_sees_jobs_beyond_the_newest_200(store):
    await _job(store, "oldest", "https://yt.test/first", "completed")
    for i in range(205):
        await _job(store, f"filler-{i}", f"https://yt.test/filler/{i}")

    found = await store.find_job_by_url("https://yt.test/first", ACTIVE)

    assert found is not None and found.job_id == "oldest"


# ── get_clip retired filter ───────────────────────────────────────────────────


async def _clip(store: Any, clip_id: str, *, retired: bool) -> None:
    await _job(store, f"job-{clip_id}", f"https://yt.test/{clip_id}")
    await store.upsert_clip(
        f"job-{clip_id}", clip_id, lambda c: c.update({"start": 0.0, "end": 1.0})
    )
    if retired:
        await _retire(store, clip_id)


async def _retire(store: Any, clip_id: str) -> None:
    """Retire a clip the way each backend does it (SQL: the janitor's flag)."""
    if isinstance(store, InMemoryJobStore):
        store._clips[clip_id]["retired"] = True
        return
    from sqlalchemy import update

    from app.db.models import ClipRecord

    async with store._factory() as session:
        await session.execute(
            update(ClipRecord).where(ClipRecord.id == clip_id).values(retired=True)
        )
        await session.commit()


async def test_get_clip_returns_live_clip(store):
    await _clip(store, "live", retired=False)

    clip = await store.get_clip("live")

    assert clip is not None and clip["clip_id"] == "live"


async def test_get_clip_hides_retired_clip_by_default(store):
    await _clip(store, "gone", retired=True)

    assert await store.get_clip("gone") is None
    assert [c["clip_id"] for c in await store.list_clips()] == []


async def test_get_clip_can_include_retired_clip(store):
    await _clip(store, "gone", retired=True)

    clip = await store.get_clip("gone", include_retired=True)

    assert clip is not None and clip["clip_id"] == "gone"


async def test_get_clip_unknown_is_none(store):
    assert await store.get_clip("missing") is None


# ── fail_interrupted_jobs (startup recovery) ─────────────────────────────────


async def test_fail_interrupted_jobs_fails_only_pending_and_running(store):
    await _job(store, "queued", "https://yt.test/q", "pending")
    await _job(store, "busy", "https://yt.test/b", "running")
    await _job(store, "done", "https://yt.test/d", "completed")
    await _job(store, "broke", "https://yt.test/f", "failed")
    await store.update("broke", lambda s: setattr(s, "error", "original error"))

    failed_ids = await store.fail_interrupted_jobs()

    assert sorted(failed_ids) == ["busy", "queued"]
    for job_id in ("queued", "busy"):
        state = await store.get(job_id)
        assert state.status == "failed"
        assert state.error == "interrupted by restart"
    assert (await store.get("done")).status == "completed"
    broke = await store.get("broke")
    assert (broke.status, broke.error) == ("failed", "original error")


async def test_interrupted_job_no_longer_blocks_its_url(store):
    await _job(store, "busy", "https://yt.test/b", "running")

    await store.fail_interrupted_jobs()

    assert await store.find_job_by_url("https://yt.test/b", ACTIVE) is None


async def test_fail_interrupted_jobs_with_nothing_to_do(store):
    await _job(store, "done", "https://yt.test/d", "completed")

    assert await store.fail_interrupted_jobs() == []
