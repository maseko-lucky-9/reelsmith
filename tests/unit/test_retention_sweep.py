"""Retention janitor (FR-013, T025): clips older than ``retention_days`` are
retired and their rendered files deleted; jobs and source videos are kept.

SQLite stores ``DateTime(timezone=True)`` values as naive wall-clock strings,
so every timestamp here is UTC and ``now`` is passed explicitly.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipRecord, JobRecord
from app.domain.ids import new_job_id
from app.services.folder_service import create_video_subfolder
from app.services.retention import sweep_expired_clips

RETENTION_DAYS = 30
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=RETENTION_DAYS, hours=1)
NEW = NOW - timedelta(days=RETENTION_DAYS) + timedelta(hours=1)


def _file(path: Path, data: bytes = b"x") -> Path:
    path.write_bytes(data)
    return path


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def seeded(factory, tmp_path):
    """One job (with a source video on disk) and four clips:
    old live, new live, old already-retired, old with an undeletable output."""
    source = _file(tmp_path / "source.mp4", b"source")
    files = {
        "old_mp4": _file(tmp_path / "old.mp4"),
        "old_jpg": _file(tmp_path / "old.jpg"),
        "new_mp4": _file(tmp_path / "new.mp4"),
        "new_jpg": _file(tmp_path / "new.jpg"),
        # An already-retired clip whose file is (unexpectedly) still on disk:
        # the sweep must not pick it up again.
        "retired_mp4": _file(tmp_path / "retired.mp4"),
    }
    async with factory() as session:
        job = JobRecord(
            youtube_url="https://x.test", video_path=str(source), created_at=OLD
        )
        session.add(job)
        await session.flush()
        old = ClipRecord(
            job_id=job.id,
            output_path=str(files["old_mp4"]),
            thumbnail_path=str(files["old_jpg"]),
            created_at=OLD,
        )
        new = ClipRecord(
            job_id=job.id,
            output_path=str(files["new_mp4"]),
            thumbnail_path=str(files["new_jpg"]),
            created_at=NEW,
        )
        retired = ClipRecord(
            job_id=job.id,
            output_path=str(files["retired_mp4"]),
            retired=True,
            created_at=OLD,
        )
        session.add_all([old, new, retired])
        await session.commit()
        ids = {"job": job.id, "old": old.id, "new": new.id, "retired": retired.id}
    return {"ids": ids, "files": files, "source": source}


async def _retired_flags(factory) -> dict[str, bool]:
    async with factory() as session:
        rows = (await session.execute(select(ClipRecord.id, ClipRecord.retired))).all()
    return {clip_id: retired for clip_id, retired in rows}


async def test_old_clip_is_retired_and_its_files_deleted(factory, seeded):
    swept = await sweep_expired_clips(factory, retention_days=RETENTION_DAYS, now=NOW)

    assert swept == [seeded["ids"]["old"]]
    assert (await _retired_flags(factory))[seeded["ids"]["old"]] is True
    assert not seeded["files"]["old_mp4"].exists()
    assert not seeded["files"]["old_jpg"].exists()


async def test_new_clip_stays_live_with_files(factory, seeded):
    await sweep_expired_clips(factory, retention_days=RETENTION_DAYS, now=NOW)

    assert (await _retired_flags(factory))[seeded["ids"]["new"]] is False
    assert seeded["files"]["new_mp4"].read_bytes() == b"x"
    assert seeded["files"]["new_jpg"].read_bytes() == b"x"


async def test_job_and_source_video_are_untouched(factory, seeded):
    await sweep_expired_clips(factory, retention_days=RETENTION_DAYS, now=NOW)

    assert seeded["source"].read_bytes() == b"source"
    async with factory() as session:
        job = await session.get(JobRecord, seeded["ids"]["job"])
    assert job is not None
    assert job.video_path == str(seeded["source"])


async def test_already_retired_clip_is_skipped(factory, seeded):
    swept = await sweep_expired_clips(factory, retention_days=RETENTION_DAYS, now=NOW)

    assert seeded["ids"]["retired"] not in swept
    assert seeded["files"]["retired_mp4"].exists()


async def test_second_sweep_is_a_no_op(factory, seeded):
    await sweep_expired_clips(factory, retention_days=RETENTION_DAYS, now=NOW)
    assert (
        await sweep_expired_clips(factory, retention_days=RETENTION_DAYS, now=NOW) == []
    )


async def test_now_in_another_offset_is_the_same_instant(factory, seeded):
    """A +02:00 ``now`` must not shift the cutoff by two hours on SQLite."""
    plus_two = NOW.astimezone(timezone(timedelta(hours=2)))

    swept = await sweep_expired_clips(
        factory, retention_days=RETENTION_DAYS, now=plus_two
    )

    assert swept == [seeded["ids"]["old"]]
    assert seeded["files"]["new_mp4"].exists()


async def test_naive_now_is_rejected(factory):
    with pytest.raises(ValueError):
        await sweep_expired_clips(
            factory, retention_days=RETENTION_DAYS, now=NOW.replace(tzinfo=None)
        )


async def test_unlink_failure_is_logged_and_does_not_abort(factory, tmp_path, caplog):
    # A directory where the video should be: unlink() raises OSError on every OS.
    blocked = tmp_path / "blocked.mp4"
    blocked.mkdir()
    blocked_jpg = _file(tmp_path / "blocked.jpg")
    other_mp4 = _file(tmp_path / "other.mp4")
    async with factory() as session:
        job = JobRecord(youtube_url="https://x.test")
        session.add(job)
        await session.flush()
        a = ClipRecord(
            job_id=job.id,
            output_path=str(blocked),
            thumbnail_path=str(blocked_jpg),
            created_at=OLD,
        )
        b = ClipRecord(job_id=job.id, output_path=str(other_mp4), created_at=OLD)
        session.add_all([a, b])
        await session.commit()
        ids = {a.id, b.id}

    with caplog.at_level(logging.ERROR, logger="app.services.retention"):
        swept = await sweep_expired_clips(
            factory, retention_days=RETENTION_DAYS, now=NOW
        )

    assert set(swept) == ids
    flags = await _retired_flags(factory)
    assert all(flags[i] for i in ids)
    # The failure on one path did not stop the rest.
    assert not blocked_jpg.exists()
    assert not other_mp4.exists()
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert str(blocked) in errors[0].getMessage()
    assert errors[0].exc_info is not None


class _CommitFails:
    """Session factory whose commit raises, as a DB outage would."""

    def __init__(self, factory):
        self._factory = factory

    def __call__(self):
        session = self._factory()

        async def _boom():
            raise RuntimeError("commit failed")

        session.commit = _boom  # type: ignore[method-assign]
        return session


async def test_files_survive_when_the_retire_commit_fails(factory, seeded):
    """Retire first, delete second: a failed commit must never leave a live
    row pointing at deleted files."""
    with pytest.raises(RuntimeError, match="commit failed"):
        await sweep_expired_clips(
            _CommitFails(factory), retention_days=RETENTION_DAYS, now=NOW
        )

    assert (await _retired_flags(factory))[seeded["ids"]["old"]] is False
    assert seeded["files"]["old_mp4"].exists()
    assert seeded["files"]["old_jpg"].exists()


async def _two_jobs_one_clip_each(factory, old_path: Path, new_path: Path) -> dict[str, str]:
    """An expired clip of an older job and a live clip of a newer job."""
    async with factory() as session:
        old_job = JobRecord(youtube_url="upload:///u/a.mp4", created_at=OLD)
        new_job = JobRecord(youtube_url="upload:///u/b.mp4", created_at=NEW)
        session.add_all([old_job, new_job])
        await session.flush()
        old = ClipRecord(job_id=old_job.id, output_path=str(old_path), created_at=OLD)
        new = ClipRecord(job_id=new_job.id, output_path=str(new_path), created_at=NEW)
        session.add_all([old, new])
        await session.commit()
        return {"old": old.id, "new": new.id}


async def test_sweeping_an_old_upload_job_keeps_the_newer_jobs_clip(factory, tmp_path):
    """T030: two upload jobs render the same clip name; with per-job folders
    the sweep of the older job cannot reach the newer job's file."""
    url = "upload:///tmp/yt/uploads/x.mp4"
    _, clips_old = create_video_subfolder(str(tmp_path), url, "upload", job_id=new_job_id())
    _, clips_new = create_video_subfolder(str(tmp_path), url, "upload", job_id=new_job_id())
    old_path = _file(Path(clips_old) / "00_Full Video.mp4", b"old")
    new_path = _file(Path(clips_new) / "00_Full Video.mp4", b"new")
    ids = await _two_jobs_one_clip_each(factory, old_path, new_path)

    swept = await sweep_expired_clips(factory, retention_days=RETENTION_DAYS, now=NOW)

    assert swept == [ids["old"]]
    assert not old_path.exists()
    assert new_path.read_bytes() == b"new"


async def test_a_path_shared_with_a_live_clip_is_not_deleted(factory, tmp_path):
    """T030: rows written before per-job folders can share one file. Retiring
    the older row must not delete the file the newer, live row points at."""
    shared = _file(tmp_path / "00_Full Video.mp4", b"newer job")
    ids = await _two_jobs_one_clip_each(factory, shared, shared)

    swept = await sweep_expired_clips(factory, retention_days=RETENTION_DAYS, now=NOW)

    assert swept == [ids["old"]]
    flags = await _retired_flags(factory)
    assert flags[ids["old"]] is True
    assert flags[ids["new"]] is False
    assert shared.read_bytes() == b"newer job"
