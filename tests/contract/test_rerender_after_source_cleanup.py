"""After the retention sweep removes a job's source (T033), the routes that
need it answer the existing 409 "source video not retained" instead of
failing on the cleared ``jobs.video_path``.

The app runs with a ``SqlJobStore`` on a throwaway SQLite file, so the
routers read the row the sweep cleared. Same ``TestClient`` set-up as
``test_clip_rerender_router.py`` and ``test_reprompt_router.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bus.job_store import SqlJobStore
from app.db.base import Base
from app.db.models import ClipRecord, JobRecord
from app.domain.models import JobState
from app.main import create_app
from app.services.retention import sweep_unused_sources

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=31)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        # Nothing consumes this queue, so nothing runs; the test inspects it.
        test_client.app.state.job_queue = asyncio.Queue()
        yield test_client


@pytest.fixture
def sql_store(client: TestClient, tmp_path: Path) -> Iterator[SqlJobStore]:
    """A SqlJobStore on a fresh SQLite file, installed as the app's store."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cleanup.db'}")

    async def _create_tables() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    client.portal.call(_create_tables)
    store = SqlJobStore.__new__(SqlJobStore)
    store._factory = async_sessionmaker(engine, expire_on_commit=False)
    client.app.state.job_store = store
    yield store
    client.portal.call(engine.dispose)


@pytest.fixture
def cleaned_job(client: TestClient, sql_store: SqlJobStore, tmp_path: Path) -> Path:
    """A completed job whose only clip is retired, idle for a month, swept."""
    root = tmp_path / "downloads"
    video = root / "Talk-abcdef12" / "Talk.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"source")

    async def _seed_and_sweep() -> list[str]:
        await sql_store.create(
            JobState(job_id="job-1", url="https://x.test", download_path=str(root))
        )

        def _complete(s: JobState) -> None:
            s.status = "completed"
            s.video_path = str(video)

        await sql_store.update("job-1", _complete)
        await sql_store.upsert_clip(
            "job-1",
            "c1",
            lambda c: c.update(
                {"output_path": str(video.parent / "00_x.mp4"), "retired": True}
            ),
        )
        async with sql_store._factory() as session:
            await session.execute(
                update(JobRecord).values(created_at=OLD, updated_at=OLD)
            )
            await session.execute(
                update(ClipRecord).values(created_at=OLD, updated_at=OLD)
            )
            await session.commit()
        return await sweep_unused_sources(
            sql_store._factory,
            retention_days=30,
            now=NOW,
            roots=(root,),
            in_flight=lambda _job_id: False,
        )

    assert client.portal.call(_seed_and_sweep) == ["job-1"]
    assert not video.exists()
    return video


def test_reprompt_of_a_cleaned_job_is_409(client, cleaned_job):
    response = client.post(
        "/jobs/job-1/reprompt", json={"start_seconds": 0, "end_seconds": 5}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "source video not retained"
    assert client.app.state.job_queue.qsize() == 0


def test_rerender_of_the_retired_clip_is_404(client, cleaned_job):
    response = client.post("/clips/c1/rerender", json={})

    assert response.status_code == 404
    assert client.app.state.job_queue.qsize() == 0


def test_rerender_of_a_clip_made_live_again_is_409(client, sql_store, cleaned_job):
    """A clip of a cleaned job can only be live again if someone restores
    it; its re-render then gets the 409, not a crash on the NULL path."""

    def _unretire(c: dict) -> None:
        c["retired"] = False

    client.portal.call(sql_store.upsert_clip, "job-1", "c1", _unretire)

    response = client.post("/clips/c1/rerender", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == "source video not retained"
    assert client.app.state.job_queue.qsize() == 0
