"""Retention sweeps under concurrent change (T033, security review:
race-condition / data integrity).

The source sweep's decision is a compare-and-set: one conditional UPDATE
(``video_path`` unchanged, job terminal, no live clip, idle) clears
``jobs.video_path``, and only a job whose UPDATE matched may lose its file,
after the commit. The other-job references and the reprompt registry are
read after that UPDATE, and the registry once more after the commit, right
before the delete. The retired-file sweep re-reads the clip row and the live
clips' paths right before each delete.

State is changed "concurrently" from another sqlite3 connection, from a hook
that runs between the sweep's selection and its claim. Every test also spies
on unlink/rmdir to prove that a lost race deletes nothing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipRecord, JobRecord
from app.services import retention
from app.services.retention import sweep_retired_files, sweep_unused_sources

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=31)
NAIVE_OLD = OLD.replace(tzinfo=None)


def _file(path: Path, data: bytes = b"x", *, age: timedelta | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if age is not None:
        ts = (NOW - age).timestamp()
        os.utime(path, (ts, ts))
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = (tmp_path / "downloads").resolve()
    (path / "uploads").mkdir(parents=True)
    return path


@pytest.fixture
def roots(root: Path) -> tuple[Path, ...]:
    return (root, root / "uploads")


@pytest.fixture
async def db(tmp_path: Path):
    """A SQLite file, so a second connection can change it mid-sweep."""
    path = tmp_path / "races.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield path, async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def deletes(monkeypatch) -> list[str]:
    """Every unlink/rmdir call, by name (calls go through)."""
    calls: list[str] = []

    def _spy(real):
        def _wrapper(target, *args, **kwargs):
            calls.append(str(target))
            return real(target, *args, **kwargs)

        return _wrapper

    monkeypatch.setattr(os, "unlink", _spy(os.unlink))
    monkeypatch.setattr(os, "rmdir", _spy(os.rmdir))
    monkeypatch.setattr(pathlib.Path, "unlink", _spy(pathlib.Path.unlink))
    monkeypatch.setattr(pathlib.Path, "rmdir", _spy(pathlib.Path.rmdir))
    return calls


def _write(db_path: Path, sql: str, *params) -> None:
    other = sqlite3.connect(db_path)
    try:
        with other:
            other.execute(sql, params)
    finally:
        other.close()


def _between_selection_and_claim(monkeypatch, target: Path, action) -> None:
    """Run ``action`` once, when the sweep checks ``target`` against the
    managed roots: after its selection, before its claim (or delete)."""
    real = retention._is_managed
    done = False

    def _check_then_act(path, roots):
        nonlocal done
        if path == target and not done:
            done = True
            action()
        return real(path, roots)

    monkeypatch.setattr(retention, "_is_managed", _check_then_act)


async def _add_job(factory, video_path: str | None) -> str:
    async with factory() as session:
        job = JobRecord(
            youtube_url="https://x.test",
            status="completed",
            video_path=video_path,
            created_at=OLD,
            updated_at=OLD,
        )
        session.add(job)
        await session.commit()
        return job.id


async def _add_clip(
    factory, job_id: str, output_path: str | None, *, retired=True
) -> str:
    async with factory() as session:
        clip = ClipRecord(
            job_id=job_id,
            retired=retired,
            output_path=output_path,
            created_at=OLD,
            updated_at=OLD,
        )
        session.add(clip)
        await session.commit()
        return clip.id


async def _job(factory, job_id: str) -> JobRecord:
    async with factory() as session:
        return await session.get(JobRecord, job_id)


async def _eligible(root: Path, factory) -> tuple[str, Path]:
    source = _file(root / "Talk-abcdef12" / "Talk.mp4", b"source")
    _file(root / "Talk-abcdef12" / "Talk.words.json", b"[]")
    job_id = await _add_job(factory, str(source))
    await _add_clip(factory, job_id, str(source.parent / "clips" / "00_Talk.mp4"))
    return job_id, source


# ── Unused sources: a lost compare-and-set deletes nothing ────────────────────


@pytest.mark.parametrize(
    "change",
    [
        "live_clip",
        "job_running",
        "same_path_elsewhere",
        "claimed_by_another_sweep",
        "reprompt",
    ],
)
async def test_a_source_claim_that_loses_a_race_deletes_nothing(
    db, root, roots, deletes, monkeypatch, change
):
    db_path, factory = db
    job_id, source = await _eligible(root, factory)
    busy: set[str] = set()
    actions = {
        "live_clip": lambda: _write(
            db_path, "UPDATE clips SET retired = 0 WHERE job_id = ?", job_id
        ),
        "job_running": lambda: _write(
            db_path, "UPDATE jobs SET status = 'running' WHERE id = ?", job_id
        ),
        "same_path_elsewhere": lambda: _write(
            db_path,
            "INSERT INTO jobs (id, youtube_url, status, video_path, created_at, "
            "updated_at) VALUES ('other', 'https://y.test', 'completed', ?, "
            "'2026-10-09 11:59:00', '2026-10-09 11:59:00')",
            str(source),
        ),
        "claimed_by_another_sweep": lambda: _write(
            db_path, "UPDATE jobs SET video_path = NULL WHERE id = ?", job_id
        ),
        "reprompt": lambda: busy.add(job_id),
    }
    _between_selection_and_claim(monkeypatch, source, actions[change])

    cleaned = await sweep_unused_sources(
        factory, retention_days=30, now=NOW, roots=roots, in_flight=busy.__contains__
    )

    assert cleaned == []
    assert deletes == []
    assert source.read_bytes() == b"source"
    job = await _job(factory, job_id)
    expected = None if change == "claimed_by_another_sweep" else str(source)
    assert job.video_path == expected
    assert job.updated_at == NAIVE_OLD


async def test_a_reprompt_that_starts_after_the_commit_keeps_the_source(
    db, root, roots, deletes, caplog
):
    """The registry is read once more after the commit, right before the
    delete. A reprompt claimed in between finds ``video_path`` NULL (409 /
    "source video not retained") until it is put back: the file is never
    deleted under it."""
    _, factory = db
    job_id, source = await _eligible(root, factory)
    calls = 0

    def _in_flight_from_the_second_look(candidate: str) -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    with caplog.at_level(logging.WARNING, logger="app.services.retention"):
        cleaned = await sweep_unused_sources(
            factory,
            retention_days=30,
            now=NOW,
            roots=roots,
            in_flight=_in_flight_from_the_second_look,
        )

    assert calls == 2
    assert cleaned == []
    assert deletes == []
    assert source.read_bytes() == b"source"
    job = await _job(factory, job_id)
    assert job.video_path == str(source)
    assert job.updated_at == NAIVE_OLD
    assert any("reprompt" in r.getMessage() for r in caplog.records)


async def test_two_concurrent_sweeps_delete_the_source_once(
    db, root, roots, deletes, caplog
):
    _, factory = db
    job_id, source = await _eligible(root, factory)

    async def _sweep() -> list[str]:
        return await sweep_unused_sources(
            factory, retention_days=30, now=NOW, roots=roots, in_flight=lambda _j: False
        )

    with caplog.at_level(logging.ERROR, logger="app.services.retention"):
        first, second = await asyncio.gather(_sweep(), _sweep())

    assert sorted(first + second) == [job_id]
    assert not source.exists()
    assert [d for d in deletes if d.endswith("Talk.mp4")] == ["Talk.mp4"]
    assert caplog.records == []
    assert (await _job(factory, job_id)).video_path is None


# ── Retired clip files: re-checked right before the delete ────────────────────


@pytest.mark.parametrize("change", ["clip_back_live", "live_clip_takes_the_path"])
async def test_a_retired_file_that_goes_live_during_the_sweep_is_kept(
    db, root, roots, deletes, monkeypatch, change
):
    db_path, factory = db
    mp4 = _file(root / "c" / "00_Talk.mp4", age=timedelta(days=2))
    job_id = await _add_job(factory, None)
    clip_id = await _add_clip(factory, job_id, str(mp4))
    actions = {
        "clip_back_live": lambda: _write(
            db_path, "UPDATE clips SET retired = 0 WHERE id = ?", clip_id
        ),
        "live_clip_takes_the_path": lambda: _write(
            db_path,
            'INSERT INTO clips (id, job_id, start, "end", output_path, liked, '
            "disliked, retired, caption_style, created_at, updated_at) VALUES "
            "('live', ?, 0, 1, ?, 0, 0, 0, 'static', '2026-10-09 11:59:00', "
            "'2026-10-09 11:59:00')",
            job_id,
            str(mp4),
        ),
    }
    _between_selection_and_claim(monkeypatch, mp4, actions[change])

    deleted = await sweep_retired_files(
        factory, grace_hours=24, now=NOW, roots=roots, in_flight=lambda _j: False
    )

    assert deleted == []
    assert deletes == []
    assert mp4.exists()
