"""Everything a job knows must survive a restart, on both stores.

``SqlJobStore`` used to persist only the columns of ``jobs``: ``title``,
``duration``, ``current_step``, the folders, the request's caption format and
aspect ratio, ``download_path`` and every chapter's artifacts were lost, so a
reopened job came back half empty. These tests read through a *fresh* store
instance over the same storage, which is what a restart does.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bus.job_store import InMemoryJobStore, SqlJobStore
from app.db import models as m
from app.db.base import Base
from app.domain.models import JobState

NewStore = Callable[[], Any]


@asynccontextmanager
async def _sql_storage(tmp_path: Path) -> AsyncIterator[NewStore]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def new_store() -> SqlJobStore:
        store = SqlJobStore()
        store._factory = factory
        return store

    try:
        yield new_store
    finally:
        await engine.dispose()


@pytest.fixture
async def sql_new_store(tmp_path: Path) -> AsyncIterator[NewStore]:
    async with _sql_storage(tmp_path) as new_store:
        yield new_store


@pytest.fixture(params=["memory", "sql"])
async def new_store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[NewStore]:
    """A factory of stores over one shared storage (memory: the same object)."""
    if request.param == "memory":
        shared = InMemoryJobStore()
        yield lambda: shared
        return
    async with _sql_storage(tmp_path) as sql_new_store:
        yield sql_new_store


def _job(**overrides: Any) -> JobState:
    fields: dict[str, Any] = {"job_id": "j1", "url": "https://yt.test/a", "download_path": "/tmp/yt"}
    return JobState(**(fields | overrides))


async def test_request_fields_set_at_creation_survive_a_restart(new_store):
    await new_store().create(_job(download_path="/data/downloads", caption_format="vtt", target_aspect_ratio=1.0))

    job = await new_store().get("j1")

    assert (job.download_path, job.caption_format, job.target_aspect_ratio) == (
        "/data/downloads",
        "vtt",
        1.0,
    )


async def test_pipeline_progress_fields_survive_a_restart(new_store):
    await new_store().create(_job())

    def mutate(s: JobState) -> None:
        s.title = "How to cook rice"
        s.duration = 612.5
        s.current_step = "rendering"
        s.destination_folder = "/data/downloads/rice"
        s.clips_folder = "/data/downloads/rice/clips"

    await new_store().update("j1", mutate)
    job = await new_store().get("j1")

    assert job.title == "How to cook rice"
    assert job.duration == 612.5
    assert job.current_step == "rendering"
    assert job.destination_folder == "/data/downloads/rice"
    assert job.clips_folder == "/data/downloads/rice/clips"


async def test_a_later_update_keeps_fields_an_earlier_one_set(new_store):
    await new_store().create(_job())
    await new_store().update("j1", lambda s: setattr(s, "title", "kept"))

    await new_store().update("j1", lambda s: setattr(s, "status", "running"))

    job = await new_store().get("j1")
    assert (job.title, job.status) == ("kept", "running")


async def test_chapter_artifacts_accumulate_across_calls_and_survive_a_restart(new_store):
    await new_store().create(_job())

    def first(c) -> None:
        c.status = "transcribing"
        c.clip_path = "/clips/0.mp4"

    def second(c) -> None:
        c.status = "completed"
        c.transcript = "hello world"
        c.image_paths = ["/img/0.jpg"]

    await new_store().upsert_chapter("j1", first, 0)
    await new_store().upsert_chapter("j1", second, 0)
    job = await new_store().get("j1")

    chapter = job.chapters[0]
    assert chapter.status == "completed"
    assert chapter.clip_path == "/clips/0.mp4", "the second call must not wipe the first"
    assert chapter.transcript == "hello world"
    assert chapter.image_paths == ["/img/0.jpg"]


async def test_updating_other_fields_leaves_chapters_alone(new_store):
    await new_store().create(_job())
    await new_store().upsert_chapter("j1", lambda c: setattr(c, "transcript", "kept"), 0)

    await new_store().update("j1", lambda s: setattr(s, "status", "completed"))

    job = await new_store().get("j1")
    assert job.chapters[0].transcript == "kept"


async def test_update_that_prunes_chapters_deletes_them(new_store):
    """Reprompt drops the replaced chapters through ``update`` (orchestrator.py)."""
    await new_store().create(_job())
    for index in (0, 1):
        await new_store().upsert_chapter("j1", lambda c: setattr(c, "transcript", "old"), index)

    def prune(s: JobState) -> None:
        s.chapters = {i: c for i, c in s.chapters.items() if i != 1}

    await new_store().update("j1", prune)

    assert set((await new_store().get("j1")).chapters) == {0}
    reborn = await new_store().upsert_chapter("j1", lambda c: None, 1)
    assert reborn.transcript is None, "a pruned chapter must not resurrect its old artifacts"


async def test_list_jobs_and_find_by_url_carry_the_stored_fields(new_store):
    await new_store().create(_job())
    await new_store().update("j1", lambda s: setattr(s, "title", "listed"))

    listed = (await new_store().list_jobs())[0]
    found = await new_store().find_job_by_url("https://yt.test/a", {"pending"})

    assert listed.title == "listed"
    assert found is not None and found.title == "listed"


# -- SQL only: rows written before this change, and damaged rows -------------


async def test_a_row_written_before_state_existed_loads_with_defaults(sql_new_store):
    store = sql_new_store()
    async with store._factory() as session:
        session.add(m.JobRecord(id="old", youtube_url="https://yt.test/old", status="completed"))
        session.add(m.ChapterRecord(job_id="old", chapter_index=0, title=""))
        await session.commit()

    job = await sql_new_store().get("old")

    assert job.status == "completed"
    assert job.title is None and job.current_step is None
    assert job.chapters == {}, "chapters without stored artifacts are left out, as before"


async def test_unreadable_state_falls_back_to_the_columns_instead_of_failing(sql_new_store):
    store = sql_new_store()
    async with store._factory() as session:
        session.add(
            m.JobRecord(
                id="bad",
                youtube_url="https://yt.test/bad",
                status="completed",
                state={"duration": "not-a-number"},
            )
        )
        await session.commit()

    jobs = await sql_new_store().list_jobs()

    assert [j.job_id for j in jobs] == ["bad"]
    assert jobs[0].duration is None


async def test_columns_win_over_a_stale_copy_in_state(sql_new_store):
    store = sql_new_store()
    async with store._factory() as session:
        session.add(
            m.JobRecord(
                id="dup",
                youtube_url="https://yt.test/dup",
                status="completed",
                state={"status": "failed", "title": "from-state"},
            )
        )
        await session.commit()

    job = await sql_new_store().get("dup")

    assert job.status == "completed"
    assert job.title == "from-state"


async def test_listing_leaves_chapter_artifacts_out_to_keep_the_poll_small(sql_new_store):
    """The home page and the badge poll the listing; they never read chapters,
    and transcripts make them the bulk of the payload."""
    await sql_new_store().create(_job())
    await sql_new_store().upsert_chapter("j1", lambda c: setattr(c, "transcript", "long " * 500), 0)

    listed = (await sql_new_store().list_jobs())[0]
    found = await sql_new_store().find_job_by_url("https://yt.test/a", {"pending"})
    full = await sql_new_store().get("j1")

    assert listed.chapters == {}
    assert found is not None and found.chapters == {}
    assert full.chapters[0].transcript.startswith("long")


async def test_one_unreadable_stored_field_does_not_cost_the_job_its_other_fields(sql_new_store):
    store = sql_new_store()
    async with store._factory() as session:
        session.add(
            m.JobRecord(
                id="part",
                youtube_url="https://yt.test/part",
                status="completed",
                state={"title": "Keep me", "clips_folder": "/clips", "duration": "oops"},
            )
        )
        session.add(
            m.ChapterRecord(
                job_id="part", chapter_index=0, title="", artifacts={"chapter_index": 0, "transcript": "kept"}
            )
        )
        await session.commit()

    job = await sql_new_store().get("part")
    await sql_new_store().update("part", lambda s: setattr(s, "status", "failed"))
    after = await sql_new_store().get("part")

    assert (job.title, job.clips_folder, job.duration) == ("Keep me", "/clips", None)
    assert job.chapters[0].transcript == "kept"
    assert (after.title, after.clips_folder) == ("Keep me", "/clips"), "a write must not wipe the readable fields"
    assert after.chapters[0].transcript == "kept"


async def test_unreadable_pipeline_options_fall_back_to_defaults_instead_of_failing_the_listing(sql_new_store):
    store = sql_new_store()
    async with store._factory() as session:
        session.add(
            m.JobRecord(
                id="opts",
                youtube_url="https://yt.test/opts",
                status="completed",
                pipeline_options={"render": "maybe"},
            )
        )
        await session.commit()

    jobs = await sql_new_store().list_jobs()

    assert [j.job_id for j in jobs] == ["opts"]
    assert jobs[0].pipeline_options.render is True
