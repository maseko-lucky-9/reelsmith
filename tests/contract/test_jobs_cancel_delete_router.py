"""Contract tests for ``POST /jobs/{id}/cancel`` and ``DELETE /jobs/{id}``
(spec 013).

Cancel: unknown job -> 404; nothing running (completed, failed, cancelled)
-> 409; a job with live pipeline tasks -> 202 ``cancelling`` and the tasks
are cancelled; a pending or running job with no task (queued, or left by a
dead process) -> 202 ``cancelled``, marked at once, with ``JobCancelled``.

Delete: a pending or running job, or one with a re-render, reprompt or
download still running -> 409 and nothing is touched; otherwise 204, files
first, then rows; an unknown job -> 204 (idempotent). Both routes answer
under ``/api`` as well (ADR-005).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain.events import EventType
from app.domain.models import JobState
from app.main import create_app
from app.services import retention
from app.workers import orchestrator as orch

JOB = "job-0001-aaaa"
URL = "https://www.youtube.com/watch?v=cancel01"


@pytest.fixture(autouse=True)
def _clean_registries():
    for registry in (orch._job_tasks, orch._user_cancelled, orch._downloads_running, orch._reprompts_in_flight):
        registry.clear()
    yield
    for registry in (orch._job_tasks, orch._user_cancelled, orch._downloads_running, orch._reprompts_in_flight):
        registry.clear()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = (tmp_path / "downloads").resolve()
    (path / "uploads").mkdir(parents=True)
    monkeypatch.setattr(retention.settings, "default_download_path", str(path))
    monkeypatch.setattr(retention.settings, "export_base_folder", str(tmp_path / "share"))
    return path


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        # Nothing consumes this queue, so no pipeline runs.
        test_client.app.state.job_queue = asyncio.Queue()
        yield test_client


def _seed(client: TestClient, *, status: str = "completed", **fields: Any) -> None:
    store = client.app.state.job_store
    state = JobState(job_id=JOB, url=URL, download_path="/unused", **fields)
    state.status = status  # type: ignore[assignment]
    client.portal.call(store.create, state)


def _status(client: TestClient) -> str:
    return client.get(f"/jobs/{JOB}").json()["status"]


def _events(client: TestClient) -> list[EventType]:
    return [e.type for e in client.app.state.event_bus._history if e.job_id == JOB]


def _live_task(client: TestClient) -> asyncio.Task[Any]:
    """A pipeline task of the job, registered as the orchestrator does."""

    async def _start() -> asyncio.Task[Any]:
        task = asyncio.create_task(asyncio.Event().wait())
        orch._track_job_task(JOB, task)
        return task

    return client.portal.call(_start)


# ── POST /jobs/{id}/cancel ────────────────────────────────────────────────────


def test_cancel_of_an_unknown_job_is_404(client):
    assert client.post("/jobs/nope/cancel").status_code == 404


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_cancel_of_a_job_with_nothing_running_is_409_and_changes_nothing(client, status):
    _seed(client, status=status)

    response = client.post(f"/jobs/{JOB}/cancel")

    assert response.status_code == 409
    assert response.json() == {"detail": f"job is {status}; nothing to cancel"}
    assert _status(client) == status
    assert _events(client) == []


def test_cancel_of_a_job_with_live_tasks_cancels_them(client):
    _seed(client, status="running")
    task = _live_task(client)

    response = client.post(f"/api/jobs/{JOB}/cancel")

    assert response.status_code == 202
    assert response.json() == {"job_id": JOB, "status": "cancelling"}
    client.portal.call(asyncio.sleep, 0.05)
    assert task.cancelled()
    # The unwinding task records the outcome; the route itself writes nothing.
    assert _status(client) == "running"


def test_cancel_of_a_completed_job_reprompting_reaches_the_reprompt(client):
    _seed(client, status="completed")
    task = _live_task(client)

    response = client.post(f"/jobs/{JOB}/cancel")

    assert response.status_code == 202
    assert response.json()["status"] == "cancelling"
    client.portal.call(asyncio.sleep, 0.05)
    assert task.cancelled()


@pytest.mark.parametrize("status", ["pending", "running"])
def test_cancel_of_a_job_with_no_task_marks_it_cancelled_at_once(client, status):
    _seed(client, status=status, current_step="download")

    response = client.post(f"/jobs/{JOB}/cancel")

    assert response.status_code == 202
    assert response.json() == {"job_id": JOB, "status": "cancelled"}
    assert _status(client) == "cancelled"
    assert _events(client) == [EventType.JOB_CANCELLED]
    event = client.app.state.event_bus._history[-1]
    assert event.payload == {"cancelled_step": "download"}


def test_the_event_stream_closes_on_job_cancelled():
    from app.routers.jobs import _TERMINAL_TYPES

    assert EventType.JOB_CANCELLED in _TERMINAL_TYPES


# ── DELETE /jobs/{id} ─────────────────────────────────────────────────────────


def _job_folder(root: Path) -> dict[str, Path]:
    folder = root / f"Talk-{JOB[:8]}"
    files = {}
    for name in ("Talk.mp4", "clips/00_Intro.mp4", "exports/manifest.json"):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        files[name] = path
    return {"folder": folder, **files}


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_delete_of_a_finished_job_removes_its_files_then_its_rows(client, root, status):
    paths = _job_folder(root)
    _seed(client, status=status, destination_folder=str(paths["folder"]), video_path=str(paths["Talk.mp4"]))
    store = client.app.state.job_store
    client.portal.call(
        store.upsert_clip, JOB, "clip-1", lambda c: c.update(output_path=str(paths["clips/00_Intro.mp4"]))
    )

    response = client.delete(f"/api/jobs/{JOB}")

    assert response.status_code == 204
    assert response.content == b""
    assert not paths["folder"].exists()
    assert client.get(f"/jobs/{JOB}").status_code == 404
    assert client.portal.call(lambda: store.get_clip("clip-1", include_retired=True)) is None


def test_delete_never_touches_the_export_folder(client, root, tmp_path):
    copy = tmp_path / "share" / JOB / "00_Intro.mp4"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"x")
    _seed(client, output_paths=[str(copy)])
    client.portal.call(client.app.state.job_store.upsert_clip, JOB, "c", lambda c: c.update(output_path=str(copy)))

    assert client.delete(f"/jobs/{JOB}").status_code == 204
    assert copy.exists()


def test_delete_is_idempotent(client, root):
    _seed(client)

    assert client.delete(f"/jobs/{JOB}").status_code == 204
    assert client.delete(f"/jobs/{JOB}").status_code == 204
    assert client.delete("/jobs/never-existed").status_code == 204


@pytest.mark.parametrize("status", ["pending", "running"])
def test_delete_of_an_active_job_is_409_and_touches_nothing(client, root, status):
    paths = _job_folder(root)
    _seed(client, status=status, destination_folder=str(paths["folder"]))

    response = client.delete(f"/jobs/{JOB}")

    assert response.status_code == 409
    assert "cancel it first" in response.json()["detail"]
    assert paths["Talk.mp4"].exists()
    assert _status(client) == status


@pytest.mark.parametrize("busy", ["task", "reprompt", "download"])
def test_delete_of_a_completed_job_with_work_running_is_409(client, root, busy):
    paths = _job_folder(root)
    _seed(client, destination_folder=str(paths["folder"]))
    if busy == "task":
        _live_task(client)  # a re-render
    elif busy == "reprompt":
        assert orch.claim_reprompt(JOB)
    else:
        orch._downloads_running.add(JOB)  # a cancelled job's download thread

    assert client.delete(f"/jobs/{JOB}").status_code == 409
    assert paths["Talk.mp4"].exists()
    assert _status(client) == "completed"


def test_a_failed_file_delete_keeps_the_rows_so_the_delete_can_be_retried(client, root, monkeypatch):
    _seed(client)

    async def _boom(job: Any, store: Any) -> list[str]:
        raise OSError("disk went away")

    monkeypatch.setattr(retention, "delete_job_files", _boom)

    with pytest.raises(OSError):
        client.delete(f"/jobs/{JOB}")
    assert client.get(f"/jobs/{JOB}").status_code == 200
