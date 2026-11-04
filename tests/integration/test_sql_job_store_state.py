"""The full-state store on a real Postgres (``reelsmith_test``).

The unit tests run the same behaviour on SQLite; Postgres stores the JSON
columns as real ``json`` and reads them back through asyncpg, so it is checked
here too. Run with: pytest -m integration (needs ``docker compose up -d --wait postgres``).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.bus.job_store import SqlJobStore
from app.domain.models import JobState

pytestmark = pytest.mark.integration


def _job(**overrides) -> JobState:
    fields = {"job_id": "pg-1", "url": "https://yt.test/pg", "download_path": "/data/downloads"}
    return JobState(**(fields | overrides))


async def test_job_state_and_chapters_survive_a_fresh_store_instance(db_store):
    await db_store.create(_job(caption_format="vtt", target_aspect_ratio=1.0))

    def progress(s: JobState) -> None:
        s.title = "How to cook rice"
        s.duration = 612.5
        s.current_step = "rendering"
        s.clips_folder = "/data/downloads/rice/clips"

    await db_store.update("pg-1", progress)
    await db_store.upsert_chapter("pg-1", lambda c: setattr(c, "clip_path", "/clips/0.mp4"), 0)
    await db_store.upsert_chapter("pg-1", lambda c: setattr(c, "transcript", "hello"), 0)

    job = await SqlJobStore().get("pg-1")

    assert (job.download_path, job.caption_format, job.target_aspect_ratio) == ("/data/downloads", "vtt", 1.0)
    assert (job.title, job.duration, job.current_step) == ("How to cook rice", 612.5, "rendering")
    assert job.clips_folder == "/data/downloads/rice/clips"
    assert (job.chapters[0].clip_path, job.chapters[0].transcript) == ("/clips/0.mp4", "hello")


async def test_pruned_chapters_are_deleted_and_listing_leaves_chapters_out(db_store):
    await db_store.create(_job())
    for index in (0, 1):
        await db_store.upsert_chapter("pg-1", lambda c: setattr(c, "transcript", "t"), index)

    await db_store.update("pg-1", lambda s: setattr(s, "chapters", {0: s.chapters[0]}))

    assert set((await db_store.get("pg-1")).chapters) == {0}
    assert (await db_store.list_jobs())[0].chapters == {}


async def test_state_is_stored_as_a_json_object(db_store):
    await db_store.create(_job())
    await db_store.update("pg-1", lambda s: setattr(s, "title", "typed"))
    await db_store.upsert_chapter("pg-1", lambda c: setattr(c, "transcript", "t"), 0)

    async with db_store._factory() as session:
        kinds = (
            await session.execute(
                text(
                    "SELECT json_typeof(j.state), json_typeof(c.artifacts)"
                    " FROM jobs j JOIN chapters c ON c.job_id = j.id"
                )
            )
        ).one()

    assert tuple(kinds) == ("object", "object")
