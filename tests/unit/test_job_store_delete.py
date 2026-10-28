"""``JobStoreProtocol.delete`` and ``list_clips(include_retired=True)``, run
against both stores (spec 013).

``SqlJobStore`` runs on an in-memory aiosqlite database, which, like the
app's engine, does not enforce foreign keys: the store must delete every
child row itself, not rely on ``ON DELETE CASCADE``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bus.job_store import InMemoryJobStore, JobNotFoundError, SqlJobStore
from app.db import models as _models  # noqa: F401 — registers tables on Base
from app.db.base import Base
from app.db.models import (
    ChapterRecord,
    ClipAnalyticsSnapshot,
    ClipEdit,
    ClipRecord,
    JobRecord,
    PublishJob,
    ScheduledPost,
    ShareLink,
    SocialAccount,
)
from app.domain.models import ChapterArtifacts, JobState


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


def _set(key: str, value: Any):
    def _mutate(c: dict[str, Any]) -> None:
        c[key] = value

    return _mutate


async def _job_with_clips(store: Any, job_id: str) -> None:
    await store.create(JobState(job_id=job_id, url=f"https://yt.test/{job_id}", download_path="/tmp"))

    def _chapter(c: ChapterArtifacts) -> None:
        c.status = "completed"

    await store.upsert_chapter(job_id, _chapter, 0)
    await store.upsert_clip(job_id, f"{job_id}-live", _set("output_path", f"/d/{job_id}/00.mp4"))
    await store.upsert_clip(job_id, f"{job_id}-old", _set("output_path", f"/d/{job_id}/old.mp4"))
    await store.retire_clips(job_id, [f"{job_id}-old"])


async def test_delete_forgets_the_job_and_all_its_clips_and_leaves_other_jobs(store):
    await _job_with_clips(store, "gone")
    await _job_with_clips(store, "kept")

    assert await store.delete("gone") is True

    with pytest.raises(JobNotFoundError):
        await store.get("gone")
    assert await store.get_clip("gone-live", include_retired=True) is None
    assert await store.get_clip("gone-old", include_retired=True) is None
    assert {c["clip_id"] for c in await store.list_clips(include_retired=True)} == {
        "kept-live",
        "kept-old",
    }
    assert (await store.get("kept")).chapters[0].status == "completed"
    assert [j.job_id for j in await store.list_jobs()] == ["kept"]


async def test_delete_of_a_missing_job_is_false_and_changes_nothing(store):
    await _job_with_clips(store, "kept")

    assert await store.delete("never-existed") is False
    assert await store.delete("never-existed") is False

    assert len(await store.list_clips(include_retired=True)) == 2


async def test_list_clips_hides_retired_clips_unless_asked(store):
    await _job_with_clips(store, "j")

    assert [c["clip_id"] for c in await store.list_clips(job_id="j")] == ["j-live"]
    assert sorted(c["clip_id"] for c in await store.list_clips(job_id="j", include_retired=True)) == [
        "j-live",
        "j-old",
    ]


async def test_sql_delete_removes_every_row_that_hangs_off_the_job():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SqlJobStore()
    store._factory = factory
    try:
        await _job_with_clips(store, "gone")
        await _job_with_clips(store, "kept")
        now = datetime.now(UTC)
        async with factory() as session:
            session.add(SocialAccount(id="acct", platform="tiktok", account_handle="h", access_token_enc=b"x"))
            for job_id in ("gone", "kept"):
                clip_id = f"{job_id}-live"
                session.add(ClipEdit(clip_id=clip_id, timeline={}))
                session.add(PublishJob(id=f"pub-{job_id}", clip_id=clip_id, social_account_id="acct"))
                session.add(ScheduledPost(publish_job_id=f"pub-{job_id}", scheduled_for=now))
                session.add(ShareLink(clip_id=clip_id, token=f"tok-{job_id}"))
                session.add(ClipAnalyticsSnapshot(clip_id=f"{job_id}-old", platform="tiktok", external_post_id="p"))
            await session.commit()

        assert await store.delete("gone") is True

        async with factory() as session:
            counts = {
                model.__tablename__: await session.scalar(select(func.count()).select_from(model))
                for model in (
                    JobRecord,
                    ChapterRecord,
                    ClipRecord,
                    ClipEdit,
                    PublishJob,
                    ScheduledPost,
                    ShareLink,
                    ClipAnalyticsSnapshot,
                    SocialAccount,
                )
            }
        # Only the kept job's rows remain; the social account is not the job's.
        assert counts == {
            "jobs": 1,
            "chapters": 1,
            "clips": 2,
            "clip_edits": 1,
            "publish_jobs": 1,
            "scheduled_posts": 1,
            "share_links": 1,
            "clip_analytics_snapshots": 1,
            "social_accounts": 1,
        }
    finally:
        await engine.dispose()


async def test_path_references_name_every_path_of_the_other_jobs_only(store):
    await store.create(JobState(job_id="gone", url="upload:///d/uploads/mine.mp4", download_path="/d"))
    await store.update("gone", lambda s: setattr(s, "video_path", "/d/uploads/mine.mp4"))
    await store.upsert_clip("gone", "gone-clip", _set("output_path", "/d/mine/00.mp4"))
    await store.create(JobState(job_id="kept", url="upload:///d/uploads/theirs.mp4", download_path="/d"))

    def _theirs(s: JobState) -> None:
        s.video_path = "/d/uploads/theirs.mp4"
        s.destination_folder = "/d/theirs"
        s.clips_folder = "/d/theirs/clips"

    await store.update("kept", _theirs)
    await store.upsert_clip("kept", "kept-live", _set("output_path", "/d/theirs/clips/00.mp4"))
    await store.upsert_clip("kept", "kept-old", _set("thumbnail_path", "/d/theirs/clips/old.jpg"))
    await store.retire_clips("kept", ["kept-old"])

    refs = await store.path_references("gone")

    assert refs.sources == ["/d/uploads/theirs.mp4"]
    assert refs.upload_files == ["/d/uploads/theirs.mp4"]
    assert sorted(refs.folders) == ["/d/theirs", "/d/theirs/clips"]
    assert set(refs.clip_files) == {"/d/theirs/clips/00.mp4", "/d/theirs/clips/old.jpg"}
