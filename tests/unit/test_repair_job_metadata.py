"""Repairing job metadata from the URL, and dropping the placeholder imports.

The 23 original jobs lost their title and duration (the old store never kept
them), so the job page says "Processing…". The title and duration are fetched
from the URL without downloading anything, and written onto the existing job.
The test-run folders that were imported from the export folder hold a 1-byte
file each; those jobs are dropped. The export folder itself is never written.
"""

from __future__ import annotations

import asyncio
import csv
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bus.job_store import SqlJobStore
from app.db.migrate import upgrade_to_head
from app.db.models import ChapterRecord, ClipRecord, JobRecord
from scripts import repair_job_metadata as repair


class Fetcher:
    """A stand-in for yt-dlp: records every URL it is asked for."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def __call__(self, url: str) -> dict[str, Any]:
        self.calls.append(url)
        answer = self.answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[tuple[async_sessionmaker, Any]]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'repair.db'}"
    await asyncio.to_thread(upgrade_to_head, url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def new_store() -> SqlJobStore:
        store = SqlJobStore()
        store._factory = factory
        return store

    yield factory, new_store
    await engine.dispose()


def _job_id() -> str:
    return uuid.uuid4().hex


async def _add_job(
    factory,
    job_id: str,
    url: str,
    *,
    state: dict | None = None,
    status: str = "completed",
    clips: tuple[Path, ...] = (),
) -> None:
    async with factory() as session:
        session.add(JobRecord(id=job_id, youtube_url=url, status=status, state=state))
        await session.flush()
        for index, path in enumerate(clips):
            chapter_id = f"{job_id}-chapter-{index}"
            session.add(
                ChapterRecord(
                    id=chapter_id, job_id=job_id, chapter_index=index, title="c", start_time=0.0, end_time=1.0
                )
            )
            session.add(ClipRecord(job_id=job_id, chapter_id=chapter_id, output_path=str(path)))
        await session.commit()


def _file(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _manifest(inbox: Path, job_id: str, thumbnail_path: str) -> None:
    folder = inbox / job_id
    folder.mkdir(parents=True)
    with (folder / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["filename", "title", "thumbnail_path", "job_id"])
        writer.writerow(["00_A.mp4", "A", thumbnail_path, job_id])


def _snapshot(root: Path) -> dict[str, tuple[int, float]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime) for p in sorted(root.rglob("*"))}


YT = "https://www.youtube.com/watch?v=abc"


# -- titles and durations from the URL ---------------------------------------


async def test_a_job_without_title_or_duration_gets_both_from_the_url(db):
    factory, new_store = db
    job = _job_id()
    await _add_job(factory, job, YT)  # a legacy row: state is NULL

    report = await repair.repair_metadata(
        factory, inbox=None, fetch=Fetcher({YT: {"title": "A Talk", "duration": 61}}), apply=True
    )

    state = await new_store().get(job)
    assert (state.title, state.duration) == ("A Talk", 61)
    assert len(report.patched) == 1


async def test_a_title_and_duration_that_are_already_set_are_never_overwritten(db):
    factory, new_store = db
    job = _job_id()
    await _add_job(factory, job, YT, state={"title": "My own title", "duration": 5})
    fetch = Fetcher({YT: {"title": "From YouTube", "duration": 99}})

    await repair.repair_metadata(factory, inbox=None, fetch=fetch, apply=True)

    state = await new_store().get(job)
    assert (state.title, state.duration) == ("My own title", 5)
    assert fetch.calls == [], "nothing is missing, so nothing is fetched"


async def test_a_missing_duration_is_filled_next_to_a_title_that_stays(db):
    factory, new_store = db
    job = _job_id()
    await _add_job(factory, job, YT, state={"title": "Kept"})

    await repair.repair_metadata(
        factory, inbox=None, fetch=Fetcher({YT: {"title": "Other", "duration": 42}}), apply=True
    )

    state = await new_store().get(job)
    assert (state.title, state.duration) == ("Kept", 42)


async def test_a_missing_title_is_filled_next_to_a_duration_that_stays(db):
    factory, new_store = db
    job = _job_id()
    await _add_job(factory, job, YT, state={"duration": 5})

    await repair.repair_metadata(
        factory, inbox=None, fetch=Fetcher({YT: {"title": "Found", "duration": 99}}), apply=True
    )

    state = await new_store().get(job)
    assert (state.title, state.duration) == ("Found", 5)


async def test_five_jobs_on_one_url_cost_one_fetch(db):
    factory, new_store = db
    tiktok = "https://www.tiktok.com/@a/video/1"
    jobs = [_job_id() for _ in range(5)]
    for job in jobs:
        await _add_job(factory, job, tiktok, status="failed")
    fetch = Fetcher({tiktok: {"title": "Caption", "duration": 12.5}})

    await repair.repair_metadata(factory, inbox=None, fetch=fetch, apply=True)

    assert fetch.calls == [tiktok]
    assert {(await new_store().get(job)).title for job in jobs} == {"Caption"}


async def test_a_failed_fetch_is_reported_and_leaves_that_job_alone_while_the_rest_proceed(db):
    factory, new_store = db
    gone, fine = _job_id(), _job_id()
    await _add_job(factory, gone, "https://www.youtube.com/watch?v=gone")
    await _add_job(factory, fine, YT)
    fetch = Fetcher(
        {
            "https://www.youtube.com/watch?v=gone": RuntimeError("Video unavailable"),
            YT: {"title": "Fine", "duration": 1},
        }
    )

    report = await repair.repair_metadata(factory, inbox=None, fetch=fetch, apply=True)

    assert (await new_store().get(gone)).title is None
    assert (await new_store().get(fine)).title == "Fine"
    assert any(gone[:8] in line and "Video unavailable" in line for line in report.skipped), report.skipped


async def test_an_upload_job_is_titled_with_its_file_stem(db):
    factory, new_store = db
    job = _job_id()
    await _add_job(factory, job, "upload:///tmp/yt/uploads/my_clip.mp4")
    fetch = Fetcher({})

    await repair.repair_metadata(factory, inbox=None, fetch=fetch, apply=True)

    assert (await new_store().get(job)).title == "my_clip"
    assert fetch.calls == [], "an upload has no web page to ask"


async def test_a_dry_run_writes_nothing_and_a_second_run_changes_nothing(db):
    factory, new_store = db
    job = _job_id()
    await _add_job(factory, job, YT)
    answers = {YT: {"title": "T", "duration": 3}}

    dry = await repair.repair_metadata(factory, inbox=None, fetch=Fetcher(answers), apply=False)
    assert len(dry.patched) == 1 and (await new_store().get(job)).title is None

    await repair.repair_metadata(factory, inbox=None, fetch=Fetcher(answers), apply=True)
    again = Fetcher(answers)
    second = await repair.repair_metadata(factory, inbox=None, fetch=again, apply=True)

    assert (second.patched, again.calls) == ([], [])


# -- inbox jobs: a title from the folder slug ---------------------------------


async def test_an_inbox_job_is_titled_from_the_slug_in_its_manifest_and_the_share_is_untouched(db, tmp_path):
    factory, new_store = db
    inbox = tmp_path / "inbox"
    job, wrong, no_slug, chosen = _job_id(), _job_id(), _job_id(), _job_id()
    _manifest(inbox, job, f"/tmp/yt/INSANE_GADGETS_TO_BEAT_THE_HEAT_WAVE-{job[:8]}/clips/00_A_thumb.jpg")
    _manifest(inbox, wrong, "/tmp/yt/SOME_OTHER_VIDEO-deadbeef/clips/00_A_thumb.jpg")
    _manifest(inbox, no_slug, "")
    _manifest(inbox, chosen, f"/tmp/yt/SLUG-{chosen[:8]}/clips/00_A_thumb.jpg")
    placeholder = {"title": f"Inbox export {job[:8]}"}
    await _add_job(factory, job, f"inbox://{job}", state=placeholder)
    await _add_job(factory, wrong, f"inbox://{wrong}", state={"title": f"Inbox export {wrong[:8]}"})
    await _add_job(factory, no_slug, f"inbox://{no_slug}", state={"title": f"Inbox export {no_slug[:8]}"})
    await _add_job(factory, chosen, f"inbox://{chosen}", state={"title": "Chosen by hand"})
    before = _snapshot(inbox)

    await repair.repair_metadata(factory, inbox=inbox, fetch=Fetcher({}), apply=True)

    assert (await new_store().get(job)).title == "INSANE GADGETS TO BEAT THE HEAT WAVE"
    assert (await new_store().get(wrong)).title == f"Inbox export {wrong[:8]}", "the id in the slug must be this job's"
    assert (await new_store().get(no_slug)).title == f"Inbox export {no_slug[:8]}"
    assert (await new_store().get(chosen)).title == "Chosen by hand"
    assert _snapshot(inbox) == before


# -- dropping the placeholder imports -----------------------------------------


async def _count(factory, model) -> int:
    async with factory() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_an_inbox_job_whose_files_are_all_placeholders_is_dropped_with_its_rows(db, tmp_path):
    factory, _ = db
    job = _job_id()
    await _add_job(factory, job, f"inbox://{job}", clips=(_file(tmp_path / "a.mp4", 1), _file(tmp_path / "b.mp4", 1)))

    report = await repair.drop_placeholders(factory, apply=True)

    assert report.dropped == [job[:8]]
    assert [await _count(factory, m) for m in (JobRecord, ChapterRecord, ClipRecord)] == [0, 0, 0]


async def test_a_dry_run_drops_nothing(db, tmp_path):
    factory, _ = db
    job = _job_id()
    await _add_job(factory, job, f"inbox://{job}", clips=(_file(tmp_path / "a.mp4", 1),))

    report = await repair.drop_placeholders(factory, apply=False)

    assert report.dropped == [job[:8]]
    assert [await _count(factory, m) for m in (JobRecord, ChapterRecord, ClipRecord)] == [1, 1, 1]


async def test_only_a_job_that_is_nothing_but_placeholders_is_dropped(db, tmp_path):
    factory, _ = db
    real, mixed, original, missing, empty = (_job_id() for _ in range(5))
    await _add_job(factory, real, f"inbox://{real}", clips=(_file(tmp_path / "real.mp4", 5000),))
    await _add_job(
        factory, mixed, f"inbox://{mixed}", clips=(_file(tmp_path / "m1.mp4", 1), _file(tmp_path / "m2.mp4", 5000))
    )
    await _add_job(factory, original, YT, clips=(_file(tmp_path / "orig.mp4", 1),))
    await _add_job(factory, missing, f"inbox://{missing}", clips=(tmp_path / "not-mounted" / "x.mp4",))
    await _add_job(factory, empty, f"inbox://{empty}")

    report = await repair.drop_placeholders(factory, apply=True)

    assert report.dropped == []
    assert await _count(factory, JobRecord) == 5, (
        "a real file, a mix, a real URL, a merely missing file (an unmounted share) and no clips all stay"
    )
