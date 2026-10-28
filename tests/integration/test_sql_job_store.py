"""Integration tests for SqlJobStore — require a running Postgres instance.

Run with: pytest -m integration
Postgres connection: YTVIDEO_TEST_DB_URL (local hosts only; see tests/db_safety.py),
default postgresql+asyncpg://reelsmith:reelsmith@localhost:5432/reelsmith
"""
from __future__ import annotations

import pytest

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

    await db_store.upsert_clip(
        "integ-score", "clip-low", lambda c: c.update({"start": 0, "end": 10, "virality_score": 20})
    )
    await db_store.upsert_clip(
        "integ-score", "clip-high", lambda c: c.update({"start": 10, "end": 20, "virality_score": 80})
    )

    high = await db_store.list_clips(job_id="integ-score", min_score=50)
    assert all(c["virality_score"] >= 50 for c in high)
    assert any(c["clip_id"] == "clip-high" for c in high)
    assert not any(c["clip_id"] == "clip-low" for c in high)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_job_by_url_exact_newest_and_status_filtered(db_store: SqlJobStore):
    url = "https://yt.test/find"
    for job_id, job_url, status in [
        ("find-old", url, "completed"),
        ("find-new", url, "running"),
        ("find-failed", url, "failed"),
        ("find-longer", url + "&t=1", "completed"),
    ]:
        await db_store.create(JobState(job_id=job_id, url=job_url, download_path="/tmp"))
        await db_store.update(job_id, lambda s, st=status: setattr(s, "status", st))

    active = ("completed", "running", "pending")
    found = await db_store.find_job_by_url(url, active)
    assert found is not None and found.job_id == "find-new"
    assert await db_store.find_job_by_url("https://yt.test/fin", active) is None
    failed = await db_store.find_job_by_url(url, ("failed",))
    assert failed is not None and failed.job_id == "find-failed"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_clip_hides_retired_unless_asked(db_store: SqlJobStore):
    from sqlalchemy import update

    from app.db.models import ClipRecord

    await db_store.create(JobState(job_id="ret-job", url="https://yt.test/r", download_path="/tmp"))
    await db_store.upsert_clip("ret-job", "ret-clip", lambda c: c.update({"start": 0, "end": 1}))
    assert (await db_store.get_clip("ret-clip"))["clip_id"] == "ret-clip"

    async with db_store._factory() as session:
        await session.execute(
            update(ClipRecord).where(ClipRecord.id == "ret-clip").values(retired=True)
        )
        await session.commit()

    assert await db_store.get_clip("ret-clip") is None
    assert (await db_store.get_clip("ret-clip", include_retired=True))["retired"] is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fail_interrupted_jobs_on_postgres(db_store: SqlJobStore):
    for job_id, status in [("int-p", "pending"), ("int-r", "running"), ("int-c", "completed")]:
        await db_store.create(JobState(job_id=job_id, url=f"https://yt.test/{job_id}", download_path="/tmp"))
        await db_store.update(job_id, lambda s, st=status: setattr(s, "status", st))

    assert sorted(await db_store.fail_interrupted_jobs()) == ["int-p", "int-r"]
    assert (await db_store.get("int-r")).error == "interrupted by restart"
    assert (await db_store.get("int-c")).status == "completed"
    assert await db_store.fail_interrupted_jobs() == []


# ── Spec 013: delete and the cancelled status on Postgres ─────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sql_delete_removes_job_and_all_children(db_store: SqlJobStore):
    import uuid
    from datetime import UTC, datetime

    from sqlalchemy import delete, func, select

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
    from app.domain.models import ChapterArtifacts

    account_id = str(uuid.uuid4())

    def _completed(c: ChapterArtifacts) -> None:
        c.status = "completed"

    for job_id in ("integ-del-gone", "integ-del-kept"):
        await db_store.create(JobState(job_id=job_id, url=f"https://yt.test/{job_id}", download_path="/tmp"))
        await db_store.upsert_chapter(job_id, _completed, 0)
        await db_store.upsert_clip(job_id, f"{job_id}-live", lambda c: c.update(output_path="/d/live.mp4"))
        await db_store.upsert_clip(job_id, f"{job_id}-old", lambda c: c.update(output_path="/d/old.mp4"))
        await db_store.retire_clips(job_id, [f"{job_id}-old"])

    children = (ClipEdit, PublishJob, ScheduledPost, ShareLink, ClipAnalyticsSnapshot)
    async with db_store._factory() as session:
        session.add(
            SocialAccount(id=account_id, platform="tiktok", account_handle="integ", access_token_enc=b"x")
        )
        await session.flush()
        for job_id in ("integ-del-gone", "integ-del-kept"):
            live = f"{job_id}-live"
            publish_id = str(uuid.uuid4())
            session.add(ClipEdit(clip_id=live, timeline={}))
            session.add(PublishJob(id=publish_id, clip_id=live, social_account_id=account_id))
            await session.flush()
            session.add(ScheduledPost(publish_job_id=publish_id, scheduled_for=datetime.now(UTC)))
            session.add(ShareLink(clip_id=live, token=f"tok-{uuid.uuid4()}"))
            session.add(ClipAnalyticsSnapshot(clip_id=f"{job_id}-old", platform="tiktok", external_post_id="p"))
        await session.commit()

    try:
        assert await db_store.delete("integ-del-gone") is True

        async def _count(model, column=None, value=None) -> int:
            async with db_store._factory() as session:
                q = select(func.count()).select_from(model)
                if column is not None:
                    q = q.where(column == value)
                return await session.scalar(q)

        assert await _count(JobRecord, JobRecord.id, "integ-del-gone") == 0
        assert await _count(ChapterRecord, ChapterRecord.job_id, "integ-del-gone") == 0
        assert await _count(ClipRecord, ClipRecord.job_id, "integ-del-gone") == 0
        # Every child table holds exactly the kept job's one row.
        for model in children:
            assert await _count(model) == 1, model.__tablename__
        assert (await db_store.get("integ-del-kept")).chapters[0].status == "completed"
        kept_clips = await db_store.list_clips(job_id="integ-del-kept", include_retired=True)
        assert {c["clip_id"] for c in kept_clips} == {"integ-del-kept-live", "integ-del-kept-old"}
        assert await db_store.delete("integ-del-gone") is False
    finally:
        async with db_store._factory() as session:
            await session.execute(delete(PublishJob).where(PublishJob.social_account_id == account_id))
            await session.execute(delete(SocialAccount).where(SocialAccount.id == account_id))
            await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_cancelled_round_trips(db_store: SqlJobStore):
    await db_store.create(JobState(job_id="integ-cancel", url="https://yt.test/c", download_path="/tmp"))
    await db_store.update("integ-cancel", lambda s: setattr(s, "status", "cancelled"))

    assert (await db_store.get("integ-cancel")).status == "cancelled"
    assert [j.status for j in await db_store.list_jobs() if j.job_id == "integ-cancel"] == ["cancelled"]
    # Final: a restart does not fail it as interrupted.
    assert "integ-cancel" not in await db_store.fail_interrupted_jobs()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_path_references_read_the_folders_out_of_the_state_json(db_store: SqlJobStore):
    await db_store.create(JobState(job_id="integ-ref-a", url="https://yt.test/a", download_path="/tmp"))
    await db_store.create(JobState(job_id="integ-ref-b", url="upload:///d/uploads/b.mp4", download_path="/tmp"))

    def _folders(s: JobState) -> None:
        s.destination_folder = "/d/b"
        s.clips_folder = "/d/b/clips"
        s.video_path = "/d/uploads/b.mp4"

    await db_store.update("integ-ref-b", _folders)

    refs = await db_store.path_references("integ-ref-a")

    assert sorted(refs.folders) == ["/d/b", "/d/b/clips"]
    assert refs.sources == ["/d/uploads/b.mp4"] and refs.upload_files == ["/d/uploads/b.mp4"]
