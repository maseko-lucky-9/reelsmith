"""Retention clean-up of unused sources and retired clip files (FR-013, T033).

``sweep_unused_sources`` removes a job's source video, its ``.words.json``
sidecar and its emptied per-job folders once the job is terminal, has no live
clip, has been idle for ``retention_days`` and for the last hour, has no
reprompt in flight, the source resolves inside a managed root, and no other
job references the same file. ``sweep_retired_files`` deletes the files of
retired clips once each file is older than the grace period.

SQLite stores ``DateTime(timezone=True)`` values as naive wall-clock strings,
so every timestamp here is UTC and ``now`` is passed explicitly. File ages
are set with ``os.utime`` relative to the same ``now``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bus.job_store import INTERRUPTIBLE_STATUSES
from app.db.base import Base
from app.db.models import ClipRecord, JobRecord
from app.domain.models import JobStatus
from app.services import retention
from app.services.platforms import upload as upload_adapter
from app.services.retention import (
    LEGACY_UPLOAD_ROOT,
    TERMINAL_JOB_STATUSES,
    RetentionReport,
    managed_roots,
    run_retention_sweeps,
    sweep_retired_files,
    sweep_unused_sources,
)
from app.settings import settings
from app.workers import orchestrator

RETENTION_DAYS = 30
GRACE_HOURS = 24
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=RETENTION_DAYS, hours=1)
RECENT = NOW - timedelta(days=RETENTION_DAYS) + timedelta(hours=1)
JUST_NOW = NOW - timedelta(minutes=10)


def _never(_job_id: str) -> bool:
    return False


def _file(path: Path, data: bytes = b"x", *, age: timedelta | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if age is not None:
        _age(path, age)
    return path


def _age(path: Path, age: timedelta) -> None:
    ts = (NOW - age).timestamp()
    os.utime(path, (ts, ts), follow_symlinks=False)


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """The managed download root, with its uploads folder."""
    path = tmp_path / "downloads"
    (path / "uploads").mkdir(parents=True)
    return path


@pytest.fixture
def roots(root: Path) -> tuple[Path, ...]:
    return (root, root / "uploads")


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    path = tmp_path / "outside"
    path.mkdir()
    return path


def _url_job_tree(root: Path, name: str = "Talk-abcdef12") -> dict[str, Path]:
    """A URL job's folder: source, sidecar and an emptied ``clips/``."""
    folder = root / name
    clips = folder / "clips"
    clips.mkdir(parents=True)
    return {
        "folder": folder,
        "clips": clips,
        "video": _file(folder / "Talk.mp4", b"source"),
        "sidecar": _file(folder / "Talk.words.json", b"[]"),
    }


async def _add_job(
    factory,
    *,
    video_path: str | None,
    status: str = "completed",
    created_at: datetime = OLD,
    updated_at: datetime | None = OLD,
    youtube_url: str = "https://x.test",
) -> str:
    async with factory() as session:
        job = JobRecord(
            youtube_url=youtube_url,
            status=status,
            video_path=video_path,
            created_at=created_at,
            updated_at=updated_at,
        )
        session.add(job)
        await session.commit()
        return job.id


async def _add_clip(
    factory,
    job_id: str,
    *,
    retired: bool = True,
    output_path: str | None = None,
    thumbnail_path: str | None = None,
    updated_at: datetime = OLD,
) -> str:
    async with factory() as session:
        clip = ClipRecord(
            job_id=job_id,
            retired=retired,
            output_path=output_path,
            thumbnail_path=thumbnail_path,
            created_at=OLD,
            updated_at=updated_at,
        )
        session.add(clip)
        await session.commit()
        return clip.id


async def _eligible_job(factory, tree: dict[str, Path], **job_kwargs) -> str:
    """A completed, idle job whose only clip is retired (its file is gone)."""
    job_id = await _add_job(factory, video_path=str(tree["video"]), **job_kwargs)
    await _add_clip(factory, job_id, output_path=str(tree["clips"] / "00_Talk.mp4"))
    return job_id


async def _video_path(factory, job_id: str) -> str | None:
    async with factory() as session:
        job = await session.get(JobRecord, job_id)
    assert job is not None
    return job.video_path


async def _updated_at(factory, job_id: str) -> datetime:
    async with factory() as session:
        job = await session.get(JobRecord, job_id)
    assert job is not None
    return job.updated_at


async def _sweep_sources(factory, roots, **kwargs) -> list[str]:
    params = {
        "retention_days": RETENTION_DAYS,
        "now": NOW,
        "roots": roots,
        "in_flight": _never,
        **kwargs,
    }
    return await sweep_unused_sources(factory, **params)


async def _sweep_files(factory, roots, **kwargs) -> list[str]:
    params = {
        "grace_hours": GRACE_HOURS,
        "now": NOW,
        "roots": roots,
        "in_flight": _never,
        **kwargs,
    }
    return await sweep_retired_files(factory, **params)


def _assert_source_kept(tree: dict[str, Path]) -> None:
    assert tree["video"].read_bytes() == b"source"
    assert tree["sidecar"].exists()


# ── A. Unused sources ─────────────────────────────────────────────────────────


def test_terminal_statuses_are_every_status_that_cannot_still_run():
    """A new JobStatus must be classified here before the sweep can see it."""
    assert set(TERMINAL_JOB_STATUSES) == set(get_args(JobStatus)) - set(
        INTERRUPTIBLE_STATUSES
    )
    assert not set(TERMINAL_JOB_STATUSES) & {"pending", "running", "queued"}


@pytest.mark.parametrize("status", ["completed", "failed"])
async def test_unused_source_sidecar_and_empty_folder_are_removed(
    factory, root, roots, status
):
    tree = _url_job_tree(root)
    job_id = await _eligible_job(factory, tree, status=status)

    cleaned = await _sweep_sources(factory, roots)

    assert cleaned == [job_id]
    assert not tree["video"].exists()
    assert not tree["sidecar"].exists()
    assert not tree["clips"].exists()
    assert not tree["folder"].exists()
    assert root.is_dir()
    assert (root / "uploads").is_dir()
    assert await _video_path(factory, job_id) is None


async def test_upload_job_folder_is_removed_but_the_uploads_root_is_kept(
    factory, root, roots, caplog
):
    """An upload's source is ``uploads/<uuid>.mp4``; its clips live in
    ``uploads/upload_video-<job8>/clips``. Both empty folders go; ``uploads``
    itself is a managed root and stays even when empty: the sweep never
    even asks to remove it (no refusal warning)."""
    upload = _file(root / "uploads" / "1f2e3d4c.mp4", b"source")
    sidecar = _file(root / "uploads" / "1f2e3d4c.words.json", b"[]")
    job_folder = root / "uploads" / "upload_video-abcdef12"
    clips = job_folder / "clips"
    clips.mkdir(parents=True)
    job_id = await _add_job(factory, video_path=str(upload))
    await _add_clip(
        factory,
        job_id,
        output_path=str(clips / "00_Full Video.mp4"),
        thumbnail_path=str(clips / "00_Full Video.jpg"),
    )

    with caplog.at_level(logging.WARNING, logger="app.services.retention"):
        assert await _sweep_sources(factory, roots) == [job_id]

    assert not upload.exists()
    assert not sidecar.exists()
    assert not job_folder.exists()
    assert (root / "uploads").is_dir()
    assert caplog.records == []


async def test_source_is_kept_while_a_clip_is_live(factory, root, roots):
    tree = _url_job_tree(root)
    job_id = await _eligible_job(factory, tree)
    await _add_clip(factory, job_id, retired=False)

    assert await _sweep_sources(factory, roots) == []

    _assert_source_kept(tree)
    assert await _video_path(factory, job_id) == str(tree["video"])


@pytest.mark.parametrize("status", ["pending", "running"])
async def test_source_is_kept_while_the_job_can_still_run(factory, root, roots, status):
    tree = _url_job_tree(root)
    job_id = await _eligible_job(factory, tree, status=status)

    assert await _sweep_sources(factory, roots) == []

    _assert_source_kept(tree)
    assert await _video_path(factory, job_id) == str(tree["video"])


async def test_source_is_kept_when_the_job_was_active_within_retention(
    factory, root, roots
):
    """Last activity is ``updated_at``: an old job touched recently stays."""
    tree = _url_job_tree(root)
    job_id = await _eligible_job(factory, tree, created_at=OLD, updated_at=RECENT)

    assert await _sweep_sources(factory, roots) == []

    _assert_source_kept(tree)
    assert await _video_path(factory, job_id) == str(tree["video"])


async def test_source_is_kept_when_a_clip_changed_within_the_last_hour(
    factory, root, roots
):
    """A re-render has no in-flight registry; it rewrites its clip row, and
    the clip sweep's retire bumps it too. Any clip row of the job touched in
    the last hour keeps the source."""
    tree = _url_job_tree(root)
    job_id = await _eligible_job(factory, tree)
    await _add_clip(factory, job_id, retired=True, updated_at=JUST_NOW)

    assert await _sweep_sources(factory, roots) == []

    _assert_source_kept(tree)


async def test_source_is_kept_when_the_job_row_changed_within_the_last_hour(
    factory, root, roots
):
    """Even with ``retention_days=0`` the job must be idle for an hour."""
    tree = _url_job_tree(root)
    await _eligible_job(factory, tree, updated_at=JUST_NOW)

    assert await _sweep_sources(factory, roots, retention_days=0) == []

    _assert_source_kept(tree)


async def test_source_outside_the_managed_roots_is_kept(factory, roots, outside):
    tree = _url_job_tree(outside)
    job_id = await _eligible_job(factory, tree)

    assert await _sweep_sources(factory, roots) == []

    _assert_source_kept(tree)
    assert tree["folder"].is_dir()
    assert await _video_path(factory, job_id) == str(tree["video"])


async def test_symlink_escaping_the_root_is_not_followed(factory, root, roots, outside):
    secret = _file(outside / "secret.mp4", b"secret")
    secret_sidecar = _file(outside / "secret.words.json", b"[]")
    folder = root / "Talk-abcdef12"
    folder.mkdir()
    link = folder / "Talk.mp4"
    link.symlink_to(secret)
    job_id = await _add_job(factory, video_path=str(link))

    assert await _sweep_sources(factory, roots) == []

    assert secret.read_bytes() == b"secret"
    assert secret_sidecar.exists()
    assert link.is_symlink()
    assert await _video_path(factory, job_id) == str(link)


async def test_dot_dot_path_escaping_the_root_is_kept(factory, root, roots, outside):
    secret = _file(outside / "secret.mp4", b"secret")
    (root / "Talk-abcdef12").mkdir()
    sneaky = f"{root}/Talk-abcdef12/../../{outside.name}/secret.mp4"
    job_id = await _add_job(factory, video_path=sneaky)

    assert await _sweep_sources(factory, roots) == []

    assert secret.read_bytes() == b"secret"
    assert await _video_path(factory, job_id) == sneaky


@pytest.mark.parametrize("same_string", [True, False])
async def test_source_shared_with_another_job_is_kept(
    factory, root, roots, same_string
):
    """Jobs from before per-job folders can share one source. The other job
    may spell the path differently (``./``); it is the same file."""
    tree = _url_job_tree(root)
    job_id = await _eligible_job(factory, tree)
    other_path = (
        str(tree["video"])
        if same_string
        else f"{tree['folder']}/./{tree['video'].name}"
    )
    other_id = await _add_job(factory, video_path=other_path)
    await _add_clip(factory, other_id, retired=False)

    assert await _sweep_sources(factory, roots) == []

    _assert_source_kept(tree)
    assert await _video_path(factory, job_id) == str(tree["video"])
    assert await _video_path(factory, other_id) == other_path
    # Undoing the clear restores the row's last activity too: no churn.
    assert await _updated_at(factory, job_id) == OLD.replace(tzinfo=None)


async def test_source_is_kept_while_a_reprompt_is_in_flight(factory, root, roots):
    tree = _url_job_tree(root)
    job_id = await _eligible_job(factory, tree)

    cleaned = await _sweep_sources(
        factory, roots, in_flight=lambda candidate: candidate == job_id
    )

    assert cleaned == []
    _assert_source_kept(tree)
    assert await _video_path(factory, job_id) == str(tree["video"])


async def test_a_folder_holding_other_files_is_not_removed(factory, root, roots):
    tree = _url_job_tree(root)
    notes = _file(tree["folder"] / "notes.txt", b"mine")
    export = _file(tree["folder"] / "exports" / "00_Talk.mp4", b"export")
    job_id = await _eligible_job(factory, tree)

    assert await _sweep_sources(factory, roots) == [job_id]

    assert not tree["video"].exists()
    assert not tree["sidecar"].exists()
    assert not tree["clips"].exists()
    assert notes.read_bytes() == b"mine"
    assert export.read_bytes() == b"export"


async def test_unlink_failure_is_logged_and_does_not_abort(
    factory, root, roots, caplog
):
    # A directory where the source should be: unlink() raises on every OS.
    blocked_folder = root / "Blocked-11111111"
    blocked = blocked_folder / "Blocked.mp4"
    blocked.mkdir(parents=True)
    blocked_id = await _add_job(factory, video_path=str(blocked))
    tree = _url_job_tree(root)
    job_id = await _eligible_job(factory, tree)

    with caplog.at_level(logging.ERROR, logger="app.services.retention"):
        cleaned = await _sweep_sources(factory, roots)

    assert set(cleaned) == {blocked_id, job_id}
    assert not tree["video"].exists()
    assert not tree["folder"].exists()
    assert await _video_path(factory, blocked_id) is None
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert str(blocked.resolve()) in errors[0].getMessage()
    assert errors[0].exc_info is not None


async def test_second_source_sweep_is_a_no_op(factory, root, roots):
    tree = _url_job_tree(root)
    job_id = await _eligible_job(factory, tree)

    assert await _sweep_sources(factory, roots) == [job_id]
    assert await _sweep_sources(factory, roots) == []


def _write(db: Path, sql: str, *params: str) -> None:
    """One committed write from another connection, as another request would."""
    other = sqlite3.connect(db)
    try:
        with other:
            other.execute(sql, params)
    finally:
        other.close()


def _before_the_clear(monkeypatch, source: Path, action) -> None:
    """Run ``action`` once, when the sweep checks ``source`` against the
    managed roots: after its candidate scan, before its clearing UPDATE."""
    real = retention._is_managed
    done = False

    def _check_then_act(path, roots):
        nonlocal done
        if path == source and not done:
            done = True
            action()
        return real(path, roots)

    monkeypatch.setattr(retention, "_is_managed", _check_then_act)


async def test_a_clip_that_goes_live_during_the_sweep_keeps_the_source(
    tmp_path, root, roots, monkeypatch
):
    """The clearing UPDATE re-checks every condition: a clip made live
    between the scan and the clear (here, from another connection) wins."""
    db = tmp_path / "race.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    file_factory = async_sessionmaker(engine, expire_on_commit=False)
    tree = _url_job_tree(root)
    job_id = await _eligible_job(file_factory, tree)

    _before_the_clear(
        monkeypatch,
        tree["video"],
        lambda: _write(db, "UPDATE clips SET retired = 0 WHERE job_id = ?", job_id),
    )

    try:
        cleaned = await _sweep_sources(file_factory, roots)
        video_path = await _video_path(file_factory, job_id)
    finally:
        await engine.dispose()

    assert cleaned == []
    _assert_source_kept(tree)
    assert video_path == str(tree["video"])


@pytest.mark.parametrize("status", ["pending", "running"])
async def test_an_upload_a_queued_job_still_needs_is_kept(factory, root, roots, status):
    """A queued or running ``upload://`` job has no ``video_path`` until its
    download step, but its URL already names the file (e.g. a retry of an
    old failed upload job)."""
    upload = _file(root / "uploads" / "1f2e3d4c.mp4", b"source")
    job_id = await _add_job(factory, video_path=str(upload), status="failed")
    await _add_job(
        factory,
        video_path=None,
        status=status,
        youtube_url=f"upload://{upload}",
        updated_at=NOW,
    )

    assert await _sweep_sources(factory, roots) == []

    assert upload.read_bytes() == b"source"
    assert await _video_path(factory, job_id) == str(upload)


async def test_an_upload_job_queued_during_the_sweep_keeps_the_source(
    tmp_path, root, roots, monkeypatch
):
    """Other jobs' references are read after the clearing UPDATE (on SQLite
    the sweep then holds the write lock), so a job queued after the scan is
    still seen, and the clear is undone."""
    db = tmp_path / "race.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    file_factory = async_sessionmaker(engine, expire_on_commit=False)
    upload = _file(root / "uploads" / "1f2e3d4c.mp4", b"source")
    job_id = await _add_job(file_factory, video_path=str(upload), status="failed")
    _before_the_clear(
        monkeypatch,
        upload,
        lambda: _write(
            db,
            "INSERT INTO jobs (id, youtube_url, status, created_at, updated_at) "
            "VALUES ('retry', ?, 'pending', '2026-10-09 11:59:00', "
            "'2026-10-09 11:59:00')",
            f"upload://{upload}",
        ),
    )

    try:
        cleaned = await _sweep_sources(file_factory, roots)
        video_path = await _video_path(file_factory, job_id)
        updated_at = await _updated_at(file_factory, job_id)
    finally:
        await engine.dispose()

    assert cleaned == []
    assert upload.read_bytes() == b"source"
    assert video_path == str(upload)
    assert updated_at == OLD.replace(tzinfo=None)


async def test_idle_jobs_that_share_a_source_are_cleaned_together(factory, root, roots):
    """Two eligible jobs that share one file do not keep it for each other."""
    tree = _url_job_tree(root)
    first = await _eligible_job(factory, tree)
    second = await _eligible_job(factory, tree)

    assert sorted(await _sweep_sources(factory, roots)) == sorted([first, second])

    assert not tree["video"].exists()
    assert await _video_path(factory, first) is None
    assert await _video_path(factory, second) is None


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


async def test_source_survives_when_the_commit_fails(factory, root, roots):
    """Clear first, delete second: a failed commit must never leave a job
    pointing at a deleted source."""
    tree = _url_job_tree(root)
    job_id = await _eligible_job(factory, tree)

    with pytest.raises(RuntimeError, match="commit failed"):
        await _sweep_sources(_CommitFails(factory), roots)

    _assert_source_kept(tree)
    assert tree["folder"].is_dir()
    assert await _video_path(factory, job_id) == str(tree["video"])


async def test_naive_now_is_rejected(factory, roots):
    with pytest.raises(ValueError):
        await _sweep_sources(factory, roots, now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        await _sweep_files(factory, roots, now=NOW.replace(tzinfo=None))


# ── B. Files of retired clips ─────────────────────────────────────────────────


async def _job_with_clip(factory, **clip_kwargs) -> tuple[str, str]:
    job_id = await _add_job(factory, video_path=None)
    return job_id, await _add_clip(factory, job_id, **clip_kwargs)


async def test_old_retired_clip_files_are_deleted(factory, root, roots):
    clips = root / "Talk-abcdef12" / "clips"
    mp4 = _file(clips / "00_Talk.mp4", age=timedelta(hours=GRACE_HOURS + 1))
    jpg = _file(clips / "00_Talk.jpg", age=timedelta(hours=GRACE_HOURS + 1))
    export = _file(
        root / "Talk-abcdef12" / "exports" / "00_Talk.mp4",
        age=timedelta(days=90),
    )
    await _job_with_clip(factory, output_path=str(mp4), thumbnail_path=str(jpg))

    deleted = await _sweep_files(factory, roots)

    assert sorted(deleted) == sorted([str(mp4.resolve()), str(jpg.resolve())])
    assert not mp4.exists()
    assert not jpg.exists()
    # exports/ is not the sweep's business (non-goal).
    assert export.exists()


async def test_recent_retired_clip_file_is_kept(factory, root, roots):
    mp4 = _file(root / "c" / "00_Talk.mp4", age=timedelta(hours=GRACE_HOURS - 1))
    await _job_with_clip(factory, output_path=str(mp4))

    assert await _sweep_files(factory, roots) == []

    assert mp4.exists()


@pytest.mark.parametrize("same_string", [True, False])
async def test_file_of_a_live_clip_is_kept(factory, root, roots, same_string):
    shared = _file(root / "c" / "00_Talk.mp4", age=timedelta(days=60))
    job_id, _ = await _job_with_clip(factory, output_path=str(shared))
    live_path = str(shared) if same_string else f"{shared.parent}/./{shared.name}"
    await _add_clip(factory, job_id, retired=False, output_path=live_path)

    assert await _sweep_files(factory, roots) == []

    assert shared.exists()


async def test_hidden_clips_of_an_in_flight_reprompt_are_kept(factory, root, roots):
    """A reprompt's new clips are retired (hidden) until its swap."""
    mp4 = _file(root / "c" / "03_New.mp4", age=timedelta(days=2))
    job_id, _ = await _job_with_clip(factory, output_path=str(mp4))

    deleted = await _sweep_files(
        factory, roots, in_flight=lambda candidate: candidate == job_id
    )

    assert deleted == []
    assert mp4.exists()


async def test_a_file_an_in_flight_reprompt_uses_is_kept_for_every_job(
    factory, root, roots
):
    """Rows from before per-job folders can share a path: another job's
    retired clip must not take a file an in-flight reprompt is rendering."""
    mp4 = _file(root / "c" / "03_New.mp4", age=timedelta(days=2))
    busy_job, _ = await _job_with_clip(factory, output_path=str(mp4))
    await _job_with_clip(factory, output_path=str(mp4))

    deleted = await _sweep_files(
        factory, roots, in_flight=lambda candidate: candidate == busy_job
    )

    assert deleted == []
    assert mp4.exists()


async def test_retired_file_outside_the_managed_roots_is_kept(
    factory, roots, outside, caplog
):
    """Skipped quietly by the sweep's own check (it is seen every tick),
    never handed to the delete that would refuse it with a warning."""
    mp4 = _file(outside / "00_Talk.mp4", age=timedelta(days=60))
    await _job_with_clip(factory, output_path=str(mp4))

    with caplog.at_level(logging.WARNING, logger="app.services.retention"):
        assert await _sweep_files(factory, roots) == []

    assert mp4.exists()
    assert caplog.records == []


async def test_retired_symlink_escaping_the_root_is_kept(
    factory, root, roots, outside, caplog
):
    secret = _file(outside / "secret.mp4", b"secret", age=timedelta(days=60))
    link = root / "c" / "00_Talk.mp4"
    link.parent.mkdir(parents=True)
    link.symlink_to(secret)
    _age(link, timedelta(days=60))
    await _job_with_clip(factory, output_path=str(link))

    with caplog.at_level(logging.WARNING, logger="app.services.retention"):
        assert await _sweep_files(factory, roots) == []

    assert secret.read_bytes() == b"secret"
    assert link.is_symlink()
    # Resolved to its outside target and skipped quietly, not refused late.
    assert caplog.records == []


async def test_retired_file_unlink_failure_is_logged_and_does_not_abort(
    factory, root, roots, caplog
):
    blocked = root / "c" / "00_Blocked.mp4"
    blocked.mkdir(parents=True)
    _age(blocked, timedelta(days=60))
    other = _file(root / "c" / "01_Other.mp4", age=timedelta(days=60))
    await _job_with_clip(factory, output_path=str(blocked))
    await _job_with_clip(factory, output_path=str(other))

    with caplog.at_level(logging.ERROR, logger="app.services.retention"):
        deleted = await _sweep_files(factory, roots)

    assert deleted == [str(other.resolve())]
    assert not other.exists()
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert str(blocked.resolve()) in errors[0].getMessage()
    assert errors[0].exc_info is not None


async def test_second_retired_file_sweep_is_a_no_op(factory, root, roots):
    mp4 = _file(root / "c" / "00_Talk.mp4", age=timedelta(days=2))
    await _job_with_clip(factory, output_path=str(mp4))

    assert await _sweep_files(factory, roots) == [str(mp4.resolve())]
    assert await _sweep_files(factory, roots) == []


# ── Roots and the janitor tick ────────────────────────────────────────────────


def test_legacy_upload_root_matches_the_upload_adapter():
    assert LEGACY_UPLOAD_ROOT.resolve() == upload_adapter._LEGACY_UPLOAD_ROOT


def test_managed_roots_are_the_download_path_its_uploads_and_the_legacy_root(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "default_download_path", str(tmp_path / "dl"))

    assert managed_roots() == (
        (tmp_path / "dl").resolve(),
        (tmp_path / "dl" / "uploads").resolve(),
        LEGACY_UPLOAD_ROOT.resolve(),
    )


@pytest.fixture
def janitor_settings(monkeypatch, root):
    monkeypatch.setattr(settings, "default_download_path", str(root))
    monkeypatch.setattr(settings, "retention_days", RETENTION_DAYS)
    monkeypatch.setattr(settings, "retired_files_grace_hours", GRACE_HOURS)


async def test_janitor_tick_runs_every_sweep_with_the_settings(
    factory, root, janitor_settings
):
    unused = _url_job_tree(root, "Unused-11111111")
    unused_id = await _eligible_job(factory, unused)
    reprompted = _file(
        root / "R-22222222" / "clips" / "00_Old.mp4", age=timedelta(days=2)
    )
    await _job_with_clip(factory, output_path=str(reprompted))
    expiring = _url_job_tree(root, "Expiring-33333333")
    expiring_id = await _add_job(factory, video_path=str(expiring["video"]))
    expiring_clip = await _add_clip(factory, expiring_id, retired=False)

    report = await run_retention_sweeps(factory, now=NOW)

    assert report.retired_clip_ids == [expiring_clip]
    assert report.cleaned_job_ids == [unused_id]
    assert report.deleted_files == [str(reprompted.resolve())]
    assert not unused["folder"].exists()
    assert not reprompted.exists()
    # The clip just retired bumped its row: the source waits out the hour.
    _assert_source_kept(expiring)


async def test_janitor_tick_follows_the_retention_and_grace_settings(
    factory, root, janitor_settings, monkeypatch
):
    monkeypatch.setattr(settings, "retention_days", 60)
    monkeypatch.setattr(settings, "retired_files_grace_hours", 72)
    unused = _url_job_tree(root, "Unused-11111111")
    await _eligible_job(factory, unused)
    reprompted = _file(
        root / "R-22222222" / "clips" / "00_Old.mp4", age=timedelta(days=2)
    )
    await _job_with_clip(factory, output_path=str(reprompted))
    expiring_id = await _add_job(factory, video_path=None)
    await _add_clip(factory, expiring_id, retired=False)

    report = await run_retention_sweeps(factory, now=NOW)

    assert report == RetentionReport([], [], [])
    _assert_source_kept(unused)
    assert reprompted.exists()


async def test_janitor_tick_honours_the_orchestrator_reprompt_claim(
    factory, root, janitor_settings
):
    tree = _url_job_tree(root)
    job_id = await _eligible_job(factory, tree)
    assert orchestrator.claim_reprompt(job_id)
    try:
        report = await run_retention_sweeps(factory, now=NOW)
    finally:
        orchestrator.release_reprompt(job_id)

    assert report.cleaned_job_ids == []
    _assert_source_kept(tree)


async def test_clip_rows_are_untouched_by_the_file_sweep(factory, root, roots):
    """Deleting a retired clip's files changes no row: the clip stays retired
    with its paths, as after the clip sweep."""
    mp4 = _file(root / "c" / "00_Talk.mp4", age=timedelta(days=2))
    _, clip_id = await _job_with_clip(factory, output_path=str(mp4))

    await _sweep_files(factory, roots)

    async with factory() as session:
        row = (
            await session.execute(
                select(ClipRecord.retired, ClipRecord.output_path).where(
                    ClipRecord.id == clip_id
                )
            )
        ).one()
    assert tuple(row) == (True, str(mp4))
