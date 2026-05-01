import pytest

from app.bus.job_store import JobNotFoundError, JobStore
from app.domain.models import JobState


@pytest.mark.asyncio
async def test_create_then_get_returns_state():
    store = JobStore()
    state = JobState(job_id="abc", url="https://example", download_path="/tmp")
    await store.create(state)
    fetched = await store.get("abc")
    assert fetched.job_id == "abc"


@pytest.mark.asyncio
async def test_get_unknown_raises():
    store = JobStore()
    with pytest.raises(JobNotFoundError):
        await store.get("missing")


@pytest.mark.asyncio
async def test_update_applies_mutation():
    store = JobStore()
    await store.create(JobState(job_id="a", url="u", download_path="/tmp"))
    await store.update("a", lambda s: setattr(s, "status", "running"))
    state = await store.get("a")
    assert state.status == "running"


@pytest.mark.asyncio
async def test_upsert_chapter_creates_then_updates():
    store = JobStore()
    await store.create(JobState(job_id="a", url="u", download_path="/tmp"))
    await store.upsert_chapter("a", lambda c: setattr(c, "transcript", "first"), chapter_index=0)
    await store.upsert_chapter("a", lambda c: setattr(c, "transcript", "second"), chapter_index=0)
    state = await store.get("a")
    assert state.chapters[0].transcript == "second"
