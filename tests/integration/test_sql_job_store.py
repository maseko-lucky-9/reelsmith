"""Integration tests for SqlJobStore — require a running Postgres instance.

Run with: pytest -m integration
Postgres connection: YTVIDEO_DB_URL or default postgresql+asyncpg://reelsmith:reelsmith@localhost:5432/reelsmith
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.bus.job_store import SqlJobStore
from app.domain.models import JobState


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_and_get(db_store: SqlJobStore):
    state = JobState(job_id="integ-1", url="https://yt.test/1", download_path="/tmp")
    await db_store.create(state)
    fetched = await db_store.get("integ-1")
    assert fetched.job_id == "integ-1"
    assert fetched.url == "https://yt.test/1"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_status(db_store: SqlJobStore):
    state = JobState(job_id="integ-2", url="https://yt.test/2", download_path="/tmp")
    await db_store.create(state)
    await db_store.update("integ-2", lambda s: setattr(s, "status", "running"))
    fetched = await db_store.get("integ-2")
    assert fetched.status == "running"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_all_ids_contains_created(db_store: SqlJobStore):
    state = JobState(job_id="integ-3", url="https://yt.test/3", download_path="/tmp")
    await db_store.create(state)
    ids = await db_store.all_ids()
    assert "integ-3" in ids


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_jobs_paginates(db_store: SqlJobStore):
    for i in range(4, 7):
        s = JobState(job_id=f"integ-{i}", url=f"https://yt.test/{i}", download_path="/tmp")
        await db_store.create(s)
    page = await db_store.list_jobs(limit=2, offset=0)
    assert len(page) <= 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_jobs_search(db_store: SqlJobStore):
    s = JobState(job_id="integ-search", url="https://yt.test/unique-keyword", download_path="/tmp")
    await db_store.create(s)
    results = await db_store.list_jobs(search="unique-keyword")
    assert any(j.job_id == "integ-search" for j in results)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upsert_and_list_clips(db_store: SqlJobStore):
    state = JobState(job_id="integ-clip", url="https://yt.test/clip", download_path="/tmp")
    await db_store.create(state)

    def mutator(c):
        c["start"] = 0.0
        c["end"] = 30.0
        c["virality_score"] = 75
        c["title"] = "Great Clip"

    await db_store.upsert_clip("integ-clip", "clip-abc", mutator)
    clips = await db_store.list_clips(job_id="integ-clip")
    assert len(clips) == 1
    assert clips[0]["virality_score"] == 75


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_clips_min_score_filter(db_store: SqlJobStore):
    state = JobState(job_id="integ-score", url="https://yt.test/score", download_path="/tmp")
    await db_store.create(state)

    await db_store.upsert_clip("integ-score", "clip-low", lambda c: c.update({"start": 0, "end": 10, "virality_score": 20}))
    await db_store.upsert_clip("integ-score", "clip-high", lambda c: c.update({"start": 10, "end": 20, "virality_score": 80}))

    high = await db_store.list_clips(job_id="integ-score", min_score=50)
    assert all(c["virality_score"] >= 50 for c in high)
    assert any(c["clip_id"] == "clip-high" for c in high)
    assert not any(c["clip_id"] == "clip-low" for c in high)
