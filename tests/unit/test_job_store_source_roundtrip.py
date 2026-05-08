"""Regression: JobState.source must survive InMemoryJobStore round-trips.

The SqlJobStore equivalent (Postgres) is exercised in integration tests; this
unit test guards the create→get→update→get loop using the in-memory store,
which still asserts the JobState model carries `source` end-to-end.
"""
from __future__ import annotations

import pytest

from app.bus.job_store import InMemoryJobStore
from app.domain.models import JobState


@pytest.mark.asyncio
async def test_source_roundtrips_through_create_and_get():
    store = InMemoryJobStore()
    state = JobState(
        job_id="job-1",
        url="https://www.tiktok.com/@u/video/1",
        download_path="/tmp",
        source="tiktok",
    )
    await store.create(state)
    fetched = await store.get("job-1")
    assert fetched.source == "tiktok"


@pytest.mark.asyncio
async def test_source_survives_update_mutator():
    store = InMemoryJobStore()
    state = JobState(
        job_id="job-2",
        url="https://www.youtube.com/watch?v=abc",
        download_path="/tmp",
        source="youtube",
    )
    await store.create(state)
    await store.update("job-2", lambda s: setattr(s, "status", "running"))
    fetched = await store.get("job-2")
    assert fetched.source == "youtube"
    assert fetched.status == "running"


@pytest.mark.asyncio
async def test_source_defaults_none_when_unset():
    store = InMemoryJobStore()
    state = JobState(job_id="job-3", url="upload:///tmp/x.mp4", download_path="/tmp")
    await store.create(state)
    fetched = await store.get("job-3")
    assert fetched.source is None
