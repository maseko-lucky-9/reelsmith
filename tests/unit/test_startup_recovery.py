"""App startup fails jobs a previous process left pending/running (SQL mode).

Without this, the duplicate-URL check would hand those dead jobs back forever.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.bus.job_store import SqlJobStore
from app.db import models as _models  # noqa: F401 — registers tables on Base
from app.db.base import Base
from app.domain.models import JobState
from app.settings import settings

URL = "https://www.youtube.com/watch?v=restart0001"


@pytest.fixture
def sql_store_with_leftovers(monkeypatch: pytest.MonkeyPatch) -> Iterator[SqlJobStore]:
    """A SqlJobStore (aiosqlite) holding what a killed process left behind."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    store = SqlJobStore()
    store._factory = async_sessionmaker(engine, expire_on_commit=False)

    async def seed() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        for job_id, url, status in [
            ("left-running", URL, "running"),
            ("left-pending", "https://www.youtube.com/watch?v=restart0002", "pending"),
            ("finished", "https://www.youtube.com/watch?v=restart0003", "completed"),
        ]:
            await store.create(JobState(job_id=job_id, url=url, download_path="/tmp"))
            await store.update(job_id, lambda s, st=status: setattr(s, "status", st))

    asyncio.run(seed())
    monkeypatch.setattr(settings, "job_store", "sql")
    monkeypatch.setattr(settings, "skip_alembic", True)
    monkeypatch.setattr(main_module, "_make_store", lambda: store)
    yield store
    asyncio.run(engine.dispose())


def _status(client: TestClient, job_id: str) -> tuple[str, str | None]:
    state: Any = client.portal.call(client.app.state.job_store.get, job_id)
    return state.status, state.error


def test_startup_fails_jobs_left_pending_or_running(sql_store_with_leftovers):
    with TestClient(main_module.create_app()) as client:
        assert _status(client, "left-running") == ("failed", "interrupted by restart")
        assert _status(client, "left-pending") == ("failed", "interrupted by restart")
        assert _status(client, "finished") == ("completed", None)


def test_job_interrupted_by_restart_does_not_block_a_new_request(
    sql_store_with_leftovers,
):
    with TestClient(main_module.create_app()) as client:
        client.app.state.job_queue = asyncio.Queue()  # don't start a pipeline

        response = client.post("/jobs", json={"url": URL, "download_path": "/tmp/x"})

        assert response.status_code == 202
        assert response.json()["status"] == "accepted"
        assert response.json()["job_id"] != "left-running"
