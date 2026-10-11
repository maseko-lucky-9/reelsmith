"""The lifespan janitor runs every retention sweep (FR-013, T033).

The app starts in SQL mode on a throwaway SQLite file (never the project's
reelsmith.db; no Alembic), with a sub-second sweep interval, and
``run_retention_sweeps`` replaced by a recorder.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import app.db.engine as db_engine
import app.db.session as db_session
import app.main as main_module
from app.db.base import Base
from app.settings import settings


def test_the_janitor_calls_run_retention_sweeps_with_an_aware_now(
    monkeypatch, tmp_path: Path
):
    db = tmp_path / "janitor.db"
    sync_engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    monkeypatch.setattr(settings, "db_url", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setattr(settings, "job_store", "sql")
    monkeypatch.setattr(settings, "skip_alembic", True)
    # A few milliseconds between ticks (the loop sleeps minutes * 60 seconds).
    monkeypatch.setattr(settings, "retention_sweep_minutes", 0.0001)
    monkeypatch.setattr(db_engine, "_engine", None)
    monkeypatch.setattr(db_session, "_factory", None)
    calls: list[tuple[object, datetime]] = []

    async def _record(factory, *, now: datetime):
        calls.append((factory, now))

    monkeypatch.setattr(main_module, "run_retention_sweeps", _record)

    with TestClient(main_module.create_app()):
        deadline = time.monotonic() + 5
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)

    assert calls, "the janitor never ran a retention sweep"
    factory, now = calls[0]
    assert factory is not None
    assert now.tzinfo is not None
