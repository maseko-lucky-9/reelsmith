"""POST /jobs defaults ``download_path`` on the server (tasks T035).

The UI no longer sends a path, so URL jobs land in
``settings.default_download_path`` instead of a hard-coded ``/tmp/yt``.
The app here has a job store and an unconsumed queue only: no orchestrator,
so nothing is downloaded.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.bus.job_store import InMemoryJobStore
from app.routers import jobs as jobs_router
from app.settings import settings

URL = "https://www.youtube.com/watch?v=xxxxxxxxxxx"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(jobs_router.router)
    app.state.job_store = InMemoryJobStore()
    app.state.job_queue = asyncio.Queue()
    return app


def test_post_job_without_download_path_uses_the_setting(tmp_path, monkeypatch):
    default = str(tmp_path / "server-default")
    monkeypatch.setattr(settings, "default_download_path", default)
    app = _app()

    with TestClient(app) as client:
        resp = client.post("/jobs", json={"url": URL})
        assert resp.status_code == 202
        job = client.get(f"/jobs/{resp.json()['job_id']}").json()

    assert job["download_path"] == default
    _job_id, payload = app.state.job_queue.get_nowait()
    assert payload["download_path"] == default


def test_post_job_with_download_path_keeps_it(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "default_download_path", str(tmp_path / "unused"))
    app = _app()

    with TestClient(app) as client:
        resp = client.post("/jobs", json={"url": URL, "download_path": str(tmp_path)})
        job = client.get(f"/jobs/{resp.json()['job_id']}").json()

    assert job["download_path"] == str(tmp_path)
