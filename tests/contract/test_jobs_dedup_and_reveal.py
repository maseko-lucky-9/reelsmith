"""POST /jobs dedup of download-only jobs and POST /jobs/{id}/reveal (spec 018)."""

from __future__ import annotations

import asyncio
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.bus.job_store import InMemoryJobStore
from app.domain.models import JobState
from app.routers import jobs as jobs_router
from app.services import retention
from app.settings import settings

URL = "https://www.facebook.com/share/r/1CMvf4y4Xi/"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(jobs_router.router)
    app.state.job_store = InMemoryJobStore()
    app.state.job_queue = asyncio.Queue()
    return app


def test_a_rendering_request_is_not_answered_with_a_download_only_job(tmp_path):
    with TestClient(_app()) as client:
        first = client.post(
            "/jobs",
            json={
                "url": URL,
                "download_path": str(tmp_path),
                "pipeline_options": {"render": False},
            },
        ).json()["job_id"]
        # The job completes without rendering anything.
        job = client.app.state.job_store
        asyncio.run(job.update(first, lambda s: setattr(s, "status", "completed")))

        second = client.post("/jobs", json={"url": URL, "download_path": str(tmp_path)}).json()

    assert second["job_id"] != first


def test_a_repeat_request_still_reuses_a_rendering_job(tmp_path):
    with TestClient(_app()) as client:
        first = client.post("/jobs", json={"url": URL, "download_path": str(tmp_path)}).json()
        second = client.post("/jobs", json={"url": URL, "download_path": str(tmp_path)}).json()

    assert second["job_id"] == first["job_id"]


def test_a_download_only_repeat_reuses_a_download_only_job(tmp_path):
    body = {"url": URL, "download_path": str(tmp_path), "pipeline_options": {"render": False}}
    with TestClient(_app()) as client:
        first = client.post("/jobs", json=body).json()
        second = client.post("/jobs", json=body).json()

    assert second["job_id"] == first["job_id"]


@pytest.fixture
def opened(monkeypatch):
    # Starlette's TestClient reports its peer as "testclient".
    monkeypatch.setattr(jobs_router, "_LOCAL_PEERS", frozenset({"testclient"}))
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(jobs_router.subprocess, "run", fake_run)
    return calls


def _job_with_folder(client: TestClient, clips_folder: str | None) -> str:
    job_id = "revealjob1"
    asyncio.run(
        client.app.state.job_store.create(
            JobState(job_id=job_id, url=URL, download_path="/unused", clips_folder=clips_folder)
        )
    )
    return job_id


def test_reveal_opens_the_clips_folder_below_the_download_path(tmp_path, monkeypatch, opened):
    monkeypatch.setattr(settings, "default_download_path", str(tmp_path))
    clips = tmp_path / "Facebook Videos" / "x-12345678" / "clips"
    clips.mkdir(parents=True)

    with TestClient(_app()) as client:
        job_id = _job_with_folder(client, str(clips))
        resp = client.post(f"/jobs/{job_id}/reveal")

    assert resp.status_code == 200
    assert resp.json() == {"folder": str(clips)}
    assert [argv[-1] for argv in opened] == [str(clips)]


def test_reveal_refuses_a_folder_outside_the_managed_paths(tmp_path, monkeypatch, opened):
    monkeypatch.setattr(settings, "default_download_path", str(tmp_path / "downloads"))
    (tmp_path / "downloads").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    with TestClient(_app()) as client:
        job_id = _job_with_folder(client, str(elsewhere))
        resp = client.post(f"/jobs/{job_id}/reveal")

    assert resp.status_code == 404
    assert opened == []


def test_reveal_refuses_the_download_path_itself(tmp_path, monkeypatch, opened):
    monkeypatch.setattr(settings, "default_download_path", str(tmp_path))

    with TestClient(_app()) as client:
        job_id = _job_with_folder(client, str(tmp_path))
        resp = client.post(f"/jobs/{job_id}/reveal")

    assert resp.status_code == 404
    assert opened == []


def test_reveal_of_an_unknown_job_is_404(opened):
    with TestClient(_app()) as client:
        assert client.post("/jobs/nope/reveal").status_code == 404
    assert opened == []


def test_reveal_is_refused_for_a_remote_peer(tmp_path, monkeypatch, opened):
    monkeypatch.setattr(settings, "default_download_path", str(tmp_path))
    clips = tmp_path / "g" / "j" / "clips"
    clips.mkdir(parents=True)

    with TestClient(_app(), client=("203.0.113.9", 5000)) as client:
        job_id = _job_with_folder(client, str(clips))
        resp = client.post(f"/jobs/{job_id}/reveal")

    assert resp.status_code == 403
    assert opened == []


def test_a_grouped_job_folder_is_a_managed_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "default_download_path", str(tmp_path))
    grouped = tmp_path / "TikTok Videos" / "x-12345678"
    grouped.mkdir(parents=True)

    assert retention.is_managed_dir(str(grouped))
    assert not retention.is_managed_dir(str(tmp_path))
    assert not retention.is_managed_dir(str(grouped / "missing"))
