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
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain.events import Event, EventType
from app.domain.models import JobState
from app.main import create_app
from app.services import retention
from app.workers import orchestrator as orch

JOB = "job-0001-aaaa"
URL = "https://www.youtube.com/watch?v=cancel01"


def _REGISTRIES():
    return (
        orch._job_tasks,
        orch._user_cancelled,
        orch._threads_running,
        orch._reprompts_in_flight,
        orch._deleting,
        orch._work_in_flight,
    )


@pytest.fixture(autouse=True)
def _clean_registries():
    for registry in _REGISTRIES():
        registry.clear()
    yield
    for registry in _REGISTRIES():
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


def _seed(client: TestClient, *, status: str = "completed", url: str = URL, **fields: Any) -> None:
    store = client.app.state.job_store
    state = JobState(job_id=JOB, url=url, download_path="/unused", **fields)
    state.status = status  # type: ignore[assignment]
    client.portal.call(store.create, state)


def _status(client: TestClient) -> str:
    return client.get(f"/jobs/{JOB}").json()["status"]


def _events(client: TestClient) -> list[EventType]:
    return [e.type for e in client.app.state.event_bus._history if e.job_id == JOB]


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition never became true"
        time.sleep(0.01)


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
    _wait_until(task.done)
    assert task.cancelled()
    # The unwinding task records the outcome; the route itself writes nothing.
    assert _status(client) == "running"


def test_cancel_of_a_completed_job_reprompting_reaches_the_reprompt(client):
    _seed(client, status="completed")
    task = _live_task(client)

    response = client.post(f"/jobs/{JOB}/cancel")

    assert response.status_code == 202
    assert response.json()["status"] == "cancelling"
    _wait_until(task.done)
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


@pytest.mark.parametrize("busy", ["task", "reprompt", "rerender claim", "thread"])
def test_delete_of_a_completed_job_with_work_running_is_409(client, root, busy):
    paths = _job_folder(root)
    _seed(client, destination_folder=str(paths["folder"]))
    if busy == "task":
        _live_task(client)  # a re-render
    elif busy == "reprompt":
        assert orch.claim_reprompt(JOB)
    elif busy == "rerender claim":
        orch.claim_work(JOB)  # a re-render queued, its task not started yet
    else:
        orch._threads_running[JOB] = 1  # a worker thread that outlived its task

    response = client.delete(f"/jobs/{JOB}")

    assert response.status_code == 409
    # Only work a cancel can stop is answered with "cancel it first"; a
    # thread still finishing is not (cancelling would be a dead end).
    if busy == "thread":
        assert response.json()["detail"] == "the job is still finishing; try again in a moment"
    else:
        assert response.json()["detail"].endswith("cancel it first")
        assert "still finishing" not in response.json()["detail"]
    assert paths["Talk.mp4"].exists()
    assert _status(client) == "completed"
    assert not orch.deleting(JOB)


def test_delete_of_a_cancelled_job_with_only_a_thread_left_says_try_again(client, root):
    _seed(client, status="cancelled")
    orch._threads_running[JOB] = 1

    response = client.delete(f"/jobs/{JOB}")

    assert (response.status_code, response.json()["detail"]) == (
        409,
        "the job is still finishing; try again in a moment",
    )


def test_a_failed_file_delete_keeps_the_rows_so_the_delete_can_be_retried(client, root, monkeypatch):
    _seed(client)

    async def _boom(job: Any, store: Any) -> list[str]:
        raise OSError("disk went away")

    monkeypatch.setattr(retention, "delete_job_files", _boom)

    with pytest.raises(OSError):
        client.delete(f"/jobs/{JOB}")
    assert client.get(f"/jobs/{JOB}").status_code == 200
    assert not orch.deleting(JOB)


# ── The event stream ends on JobCancelled and on RepromptFailed ───────────────


@pytest.mark.parametrize("terminal", [EventType.JOB_CANCELLED, EventType.REPROMPT_FAILED])
async def test_the_event_stream_ends_after_a_terminal_frame(terminal):
    from types import SimpleNamespace

    from app.bus.event_bus import AsyncEventBus
    from app.bus.job_store import InMemoryJobStore
    from app.domain.events import Event
    from app.routers import jobs as jobs_router

    bus, store = AsyncEventBus(), InMemoryJobStore()
    await store.create(JobState(job_id=JOB, url=URL, download_path="/unused"))
    for type_ in (EventType.FOLDER_CREATED, terminal, EventType.CHAPTERS_DETECTED):
        await bus.publish(Event(type=type_, job_id=JOB))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(event_bus=bus, job_store=store)))

    response = await jobs_router.stream_job_events(JOB, request)

    async def _frames() -> list[str]:
        return [frame["event"] async for frame in response.body_iterator]

    frames = await asyncio.wait_for(_frames(), timeout=5)
    assert frames == [EventType.FOLDER_CREATED.value, terminal.value]


# ── Threat T1: upload:// URLs are checked when a job is created ───────────────


@pytest.mark.parametrize(
    "target",
    ["outside the uploads folder", "missing file", "not a video", "traversal", "the uploads folder itself"],
)
def test_create_job_refuses_an_upload_url_that_is_not_an_uploaded_video(client, root, target):
    victim = root / f"Talk-{JOB[:8]}" / "Talk.mp4"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(b"x")
    (root / "uploads" / "notes.txt").write_bytes(b"x")
    path = {
        "outside the uploads folder": str(victim),
        "missing file": str(root / "uploads" / "nope.mp4"),
        "not a video": str(root / "uploads" / "notes.txt"),
        "traversal": f"{root}/uploads/../Talk-{JOB[:8]}/Talk.mp4",
        "the uploads folder itself": str(root / "uploads"),
    }[target]

    response = client.post("/jobs", json={"url": f"upload://{path}"})

    assert response.status_code == 400
    assert "uploads folder" in response.json()["detail"]
    assert client.get("/jobs").json() == []


def test_create_job_accepts_an_uploaded_video(client, root):
    upload = root / "uploads" / "1b2c.mp4"
    upload.write_bytes(b"x")

    response = client.post("/jobs", json={"url": f"upload://{upload}"})

    assert response.status_code == 202


def test_delete_of_an_upload_job_removes_its_recorded_source_and_folder(client, root):
    upload = root / "uploads" / "1b2c.mp4"
    upload.write_bytes(b"x")
    folder = root / "uploads" / f"upload_video-{JOB[:8]}"
    reel = folder / "clips" / "00_Full_Video.mp4"
    reel.parent.mkdir(parents=True)
    reel.write_bytes(b"x")
    _seed(client, url=f"upload://{upload}", video_path=str(upload), destination_folder=str(folder))

    assert client.delete(f"/jobs/{JOB}").status_code == 204
    assert not upload.exists() and not folder.exists()
    assert (root / "uploads").is_dir()


# ── R4: no re-render or reprompt while a delete is under way; R2: one at a time ─


def test_rerender_and_reprompt_answer_409_while_the_job_is_being_deleted(client, root):
    source = root / "src.mp4"
    source.write_bytes(b"x")
    _seed(client, video_path=str(source))
    client.portal.call(client.app.state.job_store.upsert_clip, JOB, "clip-1", lambda c: c.update(output_path="/x"))
    assert orch.claim_delete(JOB)

    rerender = client.post("/clips/clip-1/rerender", json={})
    reprompt = client.post(f"/jobs/{JOB}/reprompt", json={"start_seconds": 0, "end_seconds": 5})
    second_delete = client.delete(f"/jobs/{JOB}")

    assert (rerender.status_code, reprompt.status_code, second_delete.status_code) == (409, 409, 409)
    assert "being deleted" in rerender.json()["detail"] and "being deleted" in reprompt.json()["detail"]
    assert client.app.state.job_queue.qsize() == 0
    assert not orch.reprompt_in_flight(JOB)


def test_a_finished_delete_releases_its_claim(client, root):
    _seed(client, status="running")

    assert client.delete(f"/jobs/{JOB}").status_code == 409
    assert not orch.deleting(JOB)
    assert client.delete("/jobs/never-existed").status_code == 204
    assert not orch.deleting("never-existed")


async def test_two_jobs_sharing_a_source_deleted_together_leave_no_orphan(root, monkeypatch):
    """Each delete reads the other jobs' paths, then deletes. Interleaved,
    both would see the other still naming the shared source and both keep
    it; deletes are serialised, so the second one takes it."""
    import httpx

    from app.bus.job_store import InMemoryJobStore

    shared = root / "uploads" / "shared.mp4"
    shared.write_bytes(b"x")
    app = create_app()
    async with app.router.lifespan_context(app):
        store = app.state.job_store
        for job_id in ("job-a", "job-b"):
            state = JobState(job_id=job_id, url=f"upload://{shared}", download_path="/unused", video_path=str(shared))
            state.status = "completed"
            await store.create(state)
        real = InMemoryJobStore.path_references

        async def _slow(self, exclude_job_id: str):
            refs = await real(self, exclude_job_id)
            await asyncio.sleep(0.05)
            return refs

        monkeypatch.setattr(InMemoryJobStore, "path_references", _slow)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            responses = await asyncio.gather(http.delete("/jobs/job-a"), http.delete("/jobs/job-b"))

    assert [r.status_code for r in responses] == [204, 204]
    assert not shared.exists(), "the shared source was orphaned"


def test_delete_forgets_the_jobs_event_history(client, root):
    _seed(client)
    client.portal.call(client.app.state.event_bus.publish, Event(type=EventType.JOB_COMPLETED, job_id=JOB))

    assert client.delete(f"/jobs/{JOB}").status_code == 204
    assert _events(client) == []


def test_cancel_marks_a_queued_job_then_cancels_a_task_it_got_meanwhile(client, monkeypatch):
    """The queued run may get its task while the route writes the status:
    the route asks the orchestrator once more, after the write."""
    _seed(client, status="pending")
    calls: list[str] = []

    def _cancel(job_id: str) -> bool:
        calls.append(job_id)
        return False

    monkeypatch.setattr(orch, "cancel_job", _cancel)

    assert client.post(f"/jobs/{JOB}/cancel").json()["status"] == "cancelled"
    assert calls == [JOB, JOB]


# ── Review round 2 ────────────────────────────────────────────────────────────


def test_a_rerender_queued_just_before_a_delete_makes_the_delete_409(client, root):
    """The re-render's claim is taken in its route, before its task exists:
    a DELETE right after the 202 must not delete the folder under it."""
    paths = _job_folder(root)
    _seed(client, destination_folder=str(paths["folder"]), video_path=str(paths["Talk.mp4"]))
    reel = str(paths["clips/00_Intro.mp4"])
    client.portal.call(client.app.state.job_store.upsert_clip, JOB, "clip-1", lambda c: c.update(output_path=reel))

    assert client.post("/clips/clip-1/rerender", json={}).status_code == 202
    response = client.delete(f"/jobs/{JOB}")

    assert response.status_code == 409
    assert response.json()["detail"].endswith("cancel it first")
    assert paths["Talk.mp4"].exists() and _status(client) == "completed"


def test_a_rerender_that_cannot_be_queued_releases_its_claim(client, root, monkeypatch):
    source = root / "src.mp4"
    source.write_bytes(b"x")
    _seed(client, video_path=str(source))
    client.portal.call(client.app.state.job_store.upsert_clip, JOB, "clip-1", lambda c: c.update(output_path="/x"))

    async def _broken_put(item: Any) -> None:
        raise RuntimeError("queue gone")

    monkeypatch.setattr(client.app.state.job_queue, "put", _broken_put)

    with pytest.raises(RuntimeError):
        client.post("/clips/clip-1/rerender", json={})
    assert not orch.job_active(JOB)


def test_a_refused_job_folder_keeps_the_rows_and_answers_409(client, root):
    """Files below the download folder that may not be deleted safely (here
    reached through a symlink below the root): deleting the rows would leave
    files no row names, so nothing goes."""
    real = root / "real" / f"Talk-{JOB[:8]}"
    real.mkdir(parents=True)
    (real / "Talk.mp4").write_bytes(b"x")
    (root / "alias").symlink_to(root / "real", target_is_directory=True)
    _seed(client, destination_folder=str(root / "alias" / f"Talk-{JOB[:8]}"))

    response = client.delete(f"/jobs/{JOB}")

    assert response.status_code == 409
    assert "cannot be deleted safely" in response.json()["detail"]
    assert (real / "Talk.mp4").exists()
    assert client.get(f"/jobs/{JOB}").status_code == 200
    assert not orch.deleting(JOB)


def test_a_duplicate_delete_of_a_job_already_gone_is_204(client):
    """Two requests for one job: the second cannot claim it, and the first has
    already deleted it."""
    assert orch.claim_delete(JOB)  # the first request, still releasing

    assert client.delete(f"/jobs/{JOB}").status_code == 204


def test_a_duplicate_delete_of_a_job_still_being_deleted_is_409(client):
    _seed(client)
    assert orch.claim_delete(JOB)

    response = client.delete(f"/jobs/{JOB}")

    assert (response.status_code, response.json()["detail"]) == (409, "this job is already being deleted")
    assert _status(client) == "completed"


def _clip_file(root: Path) -> Path:
    clip_file = root / f"Talk-{JOB[:8]}" / "clips" / "00.mp4"
    clip_file.parent.mkdir(parents=True)
    clip_file.write_bytes(b"x")
    return clip_file


def test_enhancing_a_clip_of_a_job_being_deleted_is_409(root):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.routers import enhance_speech

    clip = SimpleNamespace(job_id=JOB, output_path=str(root / "x.mp4"))
    assert orch.claim_delete(JOB)

    with pytest.raises(HTTPException) as refused:
        enhance_speech._claim_job(clip)

    assert refused.value.status_code == 409
    assert not orch.job_has_work(JOB)


async def test_an_enhancement_counts_as_work_on_the_job_while_it_runs(root, monkeypatch):
    """A delete during an enhancement answers 409; the claim is released
    after, also when the enhancement fails."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.routers import enhance_speech

    clip_file = _clip_file(root)
    record = SimpleNamespace(id="c1", job_id=JOB, output_path=str(clip_file))
    seen: list[bool] = []

    class _Session:
        async def execute(self, _query):
            return SimpleNamespace(scalar_one_or_none=lambda: record)

        async def commit(self) -> None:
            seen.append(orch.job_active(JOB))

    def _enhance(src: str, out: str, provider: str) -> None:
        seen.append(orch.job_active(JOB))

    monkeypatch.setattr(enhance_speech.audio_enhance_service, "enhance", _enhance)
    await enhance_speech.enhance_speech("c1", session=_Session())
    assert seen == [True, True]
    assert not orch.job_active(JOB)

    def _fail(src: str, out: str, provider: str) -> None:
        raise enhance_speech.audio_enhance_service.AudioEnhanceError("boom")

    monkeypatch.setattr(enhance_speech.audio_enhance_service, "enhance", _fail)
    with pytest.raises(HTTPException):
        body = enhance_speech.EnhanceAudioRequest(provider="loudnorm")
        await enhance_speech.enhance_audio("c1", body, session=_Session())
    assert not orch.job_active(JOB)
