"""Past jobs are still there, with everything they knew, after the app restarts.

Two real app lifecycles over one database *file*: real migrations, the real
``SqlJobStore`` and the real HTTP routes. The engine singletons are reset
between them, as a new process would start without them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.db.engine as db_engine
import app.db.session as db_session
import app.main as main_module
from app.domain.models import JobState
from app.settings import settings


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A file database the app migrates itself, as on a first start."""
    db = tmp_path / "restart.db"
    monkeypatch.setattr(settings, "db_url", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setattr(settings, "job_store", "sql")
    monkeypatch.setattr(settings, "skip_alembic", False)
    return db


def _fresh_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_engine, "_engine", None)
    monkeypatch.setattr(db_session, "_factory", None)


def _call(client: TestClient, method: str, *args: Any) -> Any:
    return client.portal.call(getattr(client.app.state.job_store, method), *args)


def test_finished_job_comes_back_complete_after_a_restart(database, monkeypatch):
    _fresh_process(monkeypatch)
    with TestClient(main_module.create_app()) as first:
        _call(
            first,
            "create",
            JobState(
                job_id="done",
                url="https://yt.test/done",
                download_path="/data/downloads",
                caption_format="vtt",
            ),
        )

        def finish(s: JobState) -> None:
            s.status = "completed"
            s.title = "How to cook rice"
            s.duration = 612.5
            s.clips_folder = "/data/downloads/rice/clips"

        _call(first, "update", "done", finish)
        _call(first, "upsert_chapter", "done", lambda c: setattr(c, "transcript", "hello"), 0)

    _fresh_process(monkeypatch)
    with TestClient(main_module.create_app()) as second:
        listed = second.get("/jobs").json()
        job = second.get("/jobs/done").json()

    assert [j["job_id"] for j in listed] == ["done"]
    assert job["status"] == "completed"
    assert job["title"] == "How to cook rice"
    assert job["duration"] == 612.5
    assert job["clips_folder"] == "/data/downloads/rice/clips"
    assert (job["download_path"], job["caption_format"]) == ("/data/downloads", "vtt")
    assert job["chapters"]["0"]["transcript"] == "hello"


def test_interrupted_job_is_failed_but_keeps_what_it_had(database, monkeypatch):
    _fresh_process(monkeypatch)
    with TestClient(main_module.create_app()) as first:
        _call(first, "create", JobState(job_id="cut", url="https://yt.test/cut", download_path="/tmp"))
        _call(first, "update", "cut", lambda s: (setattr(s, "status", "running"), setattr(s, "title", "Half done")))

    _fresh_process(monkeypatch)
    with TestClient(main_module.create_app()) as second:
        job = second.get("/jobs/cut").json()

    assert job["status"] == "failed"
    assert job["error"] == "interrupted by restart"
    assert job["title"] == "Half done"
