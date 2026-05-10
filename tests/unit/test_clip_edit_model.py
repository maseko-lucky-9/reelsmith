"""Unit tests for the ``ClipEdit`` ORM model (W1.1).

Verifies:

* Round-trip through an in-memory SQLite engine (via ``Base.metadata
  .create_all`` — same surface alembic exercises in production).
* One-edit-per-clip uniqueness constraint.
* Cascade delete from ``ClipRecord`` to ``ClipEdit``.
* ``version`` defaults to 1 and is settable.
* ``timeline`` is a free-form JSON dict.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipEdit, ClipRecord, JobRecord


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed_clip(factory) -> tuple[str, str]:
    """Seed a job + clip and return (job_id, clip_id)."""
    async with factory() as session:
        job = JobRecord(youtube_url="https://example.com/v")
        session.add(job)
        await session.flush()
        clip = ClipRecord(job_id=job.id, start=0.0, end=10.0)
        session.add(clip)
        await session.commit()
        return job.id, clip.id


async def test_clip_edit_round_trip(session_factory) -> None:
    factory = session_factory
    _, clip_id = await _seed_clip(factory)

    timeline = {
        "tracks": [
            {"kind": "video", "items": [{"start": 0, "end": 10, "src": "main"}]},
            {"kind": "caption", "items": []},
            {"kind": "text-overlay", "items": []},
        ]
    }
    async with factory() as session:
        edit = ClipEdit(clip_id=clip_id, timeline=timeline)
        session.add(edit)
        await session.commit()
        edit_id = edit.id

    async with factory() as session:
        from sqlalchemy import select
        result = await session.execute(select(ClipEdit).where(ClipEdit.id == edit_id))
        loaded = result.scalar_one()
        assert loaded.clip_id == clip_id
        assert loaded.timeline == timeline
        assert loaded.version == 1
        assert loaded.created_at is not None
        assert loaded.updated_at is not None


async def test_clip_edit_unique_per_clip(session_factory) -> None:
    factory = session_factory
    _, clip_id = await _seed_clip(factory)

    async with factory() as session:
        session.add(ClipEdit(clip_id=clip_id, timeline={"tracks": []}))
        await session.commit()

    with pytest.raises(IntegrityError):
        async with factory() as session:
            session.add(ClipEdit(clip_id=clip_id, timeline={"tracks": []}))
            await session.commit()


async def test_clip_edit_cascade_on_clip_delete(session_factory) -> None:
    factory = session_factory
    _, clip_id = await _seed_clip(factory)

    async with factory() as session:
        session.add(ClipEdit(clip_id=clip_id, timeline={"tracks": []}))
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        clip = (await session.execute(
            select(ClipRecord).where(ClipRecord.id == clip_id)
        )).scalar_one()
        await session.delete(clip)
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        rows = (await session.execute(
            select(ClipEdit).where(ClipEdit.clip_id == clip_id)
        )).scalars().all()
        assert rows == [], "ClipEdit should cascade-delete with its ClipRecord"


async def test_clip_edit_version_settable(session_factory) -> None:
    factory = session_factory
    _, clip_id = await _seed_clip(factory)

    async with factory() as session:
        session.add(ClipEdit(clip_id=clip_id, timeline={"tracks": []}, version=7))
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        loaded = (await session.execute(
            select(ClipEdit).where(ClipEdit.clip_id == clip_id)
        )).scalar_one()
        assert loaded.version == 7


async def test_clip_record_edit_relationship(session_factory) -> None:
    factory = session_factory
    _, clip_id = await _seed_clip(factory)

    async with factory() as session:
        session.add(ClipEdit(clip_id=clip_id, timeline={"tracks": ["a"]}))
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        clip = (await session.execute(
            select(ClipRecord)
            .options(selectinload(ClipRecord.edit))
            .where(ClipRecord.id == clip_id)
        )).scalar_one()
        assert clip.edit is not None
        assert clip.edit.timeline == {"tracks": ["a"]}
