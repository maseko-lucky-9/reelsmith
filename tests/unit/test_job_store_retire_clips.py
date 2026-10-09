"""``JobStore.retire_clips`` on both stores (T029).

Retiring flags rows only; deleting the files is the caller's job.
``SqlJobStore`` runs on an in-memory aiosqlite database here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bus.job_store import InMemoryJobStore, SqlJobStore
from app.db import models as _models  # noqa: F401 — registers tables on Base
from app.db.base import Base
from app.domain.models import JobState


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


async def _clips(
    store: Any, job_id: str, *clip_ids: str, output_dir: Path | None = None
) -> None:
    await store.create(
        JobState(job_id=job_id, url=f"https://yt.test/{job_id}", download_path="/tmp")
    )
    for clip_id in clip_ids:
        output_path = str(output_dir / f"{clip_id}.mp4") if output_dir else None
        await store.upsert_clip(
            job_id,
            clip_id,
            lambda c, p=output_path: c.update(
                {"start": 0.0, "end": 1.0, "output_path": p}
            ),
        )


async def _live_ids(store: Any, job_id: str) -> list[str]:
    return sorted(c["clip_id"] for c in await store.list_clips(job_id=job_id))


async def test_retires_only_the_requested_clips(store):
    await _clips(store, "j1", "a", "b", "c")

    count = await store.retire_clips("j1", ["a", "c"])

    assert count == 2
    assert await _live_ids(store, "j1") == ["b"]
    assert await store.get_clip("a") is None
    assert await store.get_clip("c") is None
    assert (await store.get_clip("b")) is not None
    retired = await store.get_clip("a", include_retired=True)
    assert retired is not None and retired["retired"] is True


async def test_leaves_a_clip_of_another_job_alone(store):
    await _clips(store, "j1", "mine")
    await _clips(store, "j2", "theirs")

    count = await store.retire_clips("j1", ["mine", "theirs"])

    assert count == 1
    assert await _live_ids(store, "j2") == ["theirs"]
    assert (await store.get_clip("theirs")) is not None


async def test_second_call_retires_nothing(store):
    await _clips(store, "j1", "a", "b")

    assert await store.retire_clips("j1", ["a", "b"]) == 2
    assert await store.retire_clips("j1", ["a", "b"]) == 0
    assert await _live_ids(store, "j1") == []


async def test_counts_only_clips_not_already_retired(store):
    await _clips(store, "j1", "a", "b")
    await store.retire_clips("j1", ["a"])

    assert await store.retire_clips("j1", ["a", "b"]) == 1


async def test_ignores_unknown_ids_and_empty_input(store):
    await _clips(store, "j1", "a")

    assert await store.retire_clips("j1", ["nope", "missing"]) == 0
    assert await store.retire_clips("j1", []) == 0
    assert await _live_ids(store, "j1") == ["a"]


async def test_duplicate_ids_are_counted_once(store):
    await _clips(store, "j1", "a")

    assert await store.retire_clips("j1", ["a", "a"]) == 1


async def test_does_not_delete_clip_files(store, tmp_path):
    await _clips(store, "j1", "a", output_dir=tmp_path)
    (tmp_path / "a.mp4").write_bytes(b"clip")

    await store.retire_clips("j1", ["a"])

    assert (tmp_path / "a.mp4").read_bytes() == b"clip"
