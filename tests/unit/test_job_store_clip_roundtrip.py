"""Every writable clip field survives the JobStore, on both stores (T029).

The SQL store used to drop columns it did not map (``ai_hook_text``,
``broll_assets``, ...), and a later ``upsert_clip`` wiped them again because
the reload did not carry them. Each field is checked on its own, through every
read path, so a dropped mapping names the field that broke.

``SqlJobStore`` runs on an in-memory aiosqlite database here.
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

JOB_ID = "job-roundtrip"
CLIP_ID = "clip-roundtrip"

# A distinctive non-default value for every clip column the pipeline writes.
CLIP_VALUES: dict[str, Any] = {
    "start": 12.5,
    "end": 47.25,
    "output_path": "/out/job-roundtrip/clips/01_Hook.mp4",
    "thumbnail_path": "/out/job-roundtrip/thumbs/01_Hook.jpg",
    "title": "The hook that works",
    "summary": "A one-line summary of the clip.",
    "hashtags": ["#reels", "#hook"],
    "virality_score": 87,
    "score_breakdown": {"hook": 9, "pace": 7, "payoff": 8},
    "transcript": {"text": "hello world", "words": [{"w": "hello", "s": 0.0}]},
    "liked": True,
    "disliked": True,
    "ai_hook_text": "Wait until you see the end",
    "ai_hook_audio_path": "/out/job-roundtrip/hooks/01.mp3",
    "broll_assets": [{"query": "city", "url": "https://pexels.test/1.mp4"}],
    "caption_style": "karaoke",
    "captions_burnt_path": "/out/job-roundtrip/captions/01.mp4",
}
FIELDS = sorted(CLIP_VALUES)


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


async def _clip_with_every_field(store: Any) -> None:
    await store.create(
        JobState(job_id=JOB_ID, url="https://yt.test/roundtrip", download_path="/tmp")
    )
    await store.upsert_clip(JOB_ID, CLIP_ID, lambda c: c.update(CLIP_VALUES))


@pytest.mark.parametrize("field", FIELDS)
async def test_get_clip_returns_every_field(store, field):
    await _clip_with_every_field(store)

    clip = await store.get_clip(CLIP_ID)

    assert clip is not None
    assert clip[field] == CLIP_VALUES[field]


@pytest.mark.parametrize("field", FIELDS)
async def test_list_clips_returns_every_field(store, field):
    await _clip_with_every_field(store)

    clips = await store.list_clips(job_id=JOB_ID)

    assert [c["clip_id"] for c in clips] == [CLIP_ID]
    assert clips[0][field] == CLIP_VALUES[field]


@pytest.mark.parametrize("field", FIELDS)
async def test_later_upsert_keeps_every_field(store, field):
    """A mutator that touches one key must not wipe the others (the pipeline
    sets the AI hook, then summary and hashtags, in separate upserts)."""
    await _clip_with_every_field(store)

    seen = await store.upsert_clip(
        JOB_ID, CLIP_ID, lambda c: c.update({"title": "new"})
    )
    clip = await store.get_clip(CLIP_ID)

    expected = "new" if field == "title" else CLIP_VALUES[field]
    assert seen[field] == expected
    assert clip is not None and clip[field] == expected


async def test_upsert_can_retire_and_restore_a_clip(store):
    await _clip_with_every_field(store)

    await store.upsert_clip(JOB_ID, CLIP_ID, lambda c: c.update({"retired": True}))

    assert await store.get_clip(CLIP_ID) is None
    retired = await store.get_clip(CLIP_ID, include_retired=True)
    assert retired is not None and retired["retired"] is True

    await store.upsert_clip(JOB_ID, CLIP_ID, lambda c: c.update({"retired": False}))

    assert [c["clip_id"] for c in await store.list_clips(job_id=JOB_ID)] == [CLIP_ID]
