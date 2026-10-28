"""User cancel versus shutdown in the orchestrator (spec 013 FR-001..FR-005).

``cancel_job`` cancels every pipeline task of one job. Only a job the user
cancelled ends ``cancelled`` with ``JobCancelled`` (a run), ``RepromptFailed``
with ``reason="cancelled"`` (a reprompt) or its chapter put back (a
re-render). A shutdown (the orchestrator task cancelled) keeps today's
behaviour: the job's status is left as it was and nothing is emitted.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import InMemoryJobStore
from app.domain.events import Event, EventType
from app.domain.models import ChapterArtifacts, JobState
from app.services.platforms._yt_dlp_base import download_stop
from app.services.platforms.base import Chapter, DownloadResult
from app.workers import orchestrator as orch

URL = "https://www.youtube.com/watch?v=cancel01"
JOB = "job-cancel"


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


class _Adapter:
    platform_id = "youtube"

    def download(self, url: str, destination_folder: str) -> DownloadResult:
        video_path = str(Path(destination_folder) / "video.mp4")
        Path(video_path).write_bytes(b"\x00")
        return DownloadResult(
            video_path=video_path, info={"title": "T", "duration": 12.0}, title="T", duration=12.0, source="youtube"
        )

    def extract_chapters(self, info: dict[str, Any]) -> list[Chapter]:
        return [Chapter(index=0, title="Only", start=0.0, end=12.0)]


class _Blocker:
    """Stand-in for ``_process_chapter``: marks the chapter ``rendering`` and
    blocks until cancelled."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cleanup_roots: list[Path] = []

    async def __call__(self, *, chapter: dict[str, Any], job_id: str, store: Any, cleanup_root: Path, **_: Any):
        def _rendering(c: ChapterArtifacts) -> None:
            c.status = "rendering"

        await store.upsert_chapter(job_id, _rendering, int(chapter["index"]))
        self.cleanup_roots.append(cleanup_root)
        self.entered.set()
        await asyncio.Event().wait()


def _fake_subfolder(download_path: str, url: str, platform_id: str = "video", job_id: str | None = None):
    clips = Path(download_path) / f"vid-{job_id}" / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    return str(clips.parent), str(clips)


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Blocker:
    blocker = _Blocker()
    monkeypatch.setattr(orch.settings, "export_base_folder", str(tmp_path / "exports"))
    monkeypatch.setattr(orch.settings, "max_concurrent_jobs", 1)
    monkeypatch.setattr(orch.folder_service, "create_video_subfolder", _fake_subfolder)
    monkeypatch.setattr(orch, "resolve_adapter", lambda url: _Adapter())
    monkeypatch.setattr(orch.clip_service, "probe_safe_end", lambda path: 12.0)
    monkeypatch.setattr(orch, "_process_chapter", blocker)
    return blocker


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)
    await asyncio.sleep(0.02)


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)


async def _stop(task: asyncio.Task[Any]) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _create(store: InMemoryJobStore, job_id: str, tmp_path: Path) -> None:
    await store.create(JobState(job_id=job_id, url=URL, source="youtube", download_path=str(tmp_path)))


async def _request(bus: AsyncEventBus, job_id: str, tmp_path: Path, **payload: Any) -> None:
    await bus.publish(
        Event(
            type=EventType.VIDEO_REQUESTED,
            job_id=job_id,
            payload={"url": URL, "download_path": str(tmp_path), **payload},
        )
    )


def _types(bus: AsyncEventBus, job_id: str) -> list[EventType]:
    return [e.type for e in bus._history if e.job_id == job_id]


# ── A running job ─────────────────────────────────────────────────────────────


async def test_user_cancel_marks_a_running_job_cancelled_and_emits_job_cancelled_last(pipeline, tmp_path):
    bus, store = AsyncEventBus(), InMemoryJobStore()
    await _create(store, JOB, tmp_path)
    runner = asyncio.create_task(orch.run_orchestrator(bus, store))
    try:
        await _request(bus, JOB, tmp_path)
        await asyncio.wait_for(pipeline.entered.wait(), 5)
        assert orch.job_active(JOB)

        assert orch.cancel_job(JOB) is True
        await _until(lambda: not orch.job_active(JOB))

        job = await store.get(JOB)
        assert job.status == "cancelled"
        types = _types(bus, JOB)
        assert types[-1] is EventType.JOB_CANCELLED
        assert types.count(EventType.JOB_CANCELLED) == 1
        assert EventType.JOB_FAILED not in types
        cancelled = next(e for e in bus._history if e.type is EventType.JOB_CANCELLED)
        assert cancelled.payload == {"cancelled_step": "chapters"}
        # The job's _tmp folder is cleaned, and the registries are empty again.
        assert pipeline.cleanup_roots and not pipeline.cleanup_roots[0].exists()
        assert orch._job_tasks == {} and orch._user_cancelled == set()
        assert not runner.done(), "a user cancel must not stop the orchestrator"
    finally:
        await _stop(runner)


async def test_shutdown_leaves_a_running_job_as_it_was(pipeline, tmp_path):
    bus, store = AsyncEventBus(), InMemoryJobStore()
    await _create(store, JOB, tmp_path)
    runner = asyncio.create_task(orch.run_orchestrator(bus, store))
    await _request(bus, JOB, tmp_path)
    await asyncio.wait_for(pipeline.entered.wait(), 5)

    await asyncio.wait_for(_stop(runner), 5)

    assert (await store.get(JOB)).status == "running"
    types = _types(bus, JOB)
    assert EventType.JOB_CANCELLED not in types
    assert EventType.JOB_FAILED not in types
    assert pipeline.cleanup_roots and not pipeline.cleanup_roots[0].exists()


async def test_cancelling_one_job_leaves_the_other_running(pipeline, tmp_path, monkeypatch):
    monkeypatch.setattr(orch.settings, "max_concurrent_jobs", 2)
    bus, store = AsyncEventBus(), InMemoryJobStore()
    await _create(store, "job-a", tmp_path)
    await _create(store, "job-b", tmp_path)
    runner = asyncio.create_task(orch.run_orchestrator(bus, store))
    try:
        await _request(bus, "job-a", tmp_path)
        await _request(bus, "job-b", tmp_path)
        await _until(lambda: len(pipeline.cleanup_roots) == 2)

        orch.cancel_job("job-a")
        await _until(lambda: not orch.job_active("job-a"))

        assert (await store.get("job-a")).status == "cancelled"
        assert (await store.get("job-b")).status == "running"
        assert orch.job_active("job-b")
    finally:
        await _stop(runner)


# ── A job waiting for a slot, or still in the queue ───────────────────────────


async def test_user_cancel_of_a_job_waiting_for_a_slot_marks_it_cancelled(pipeline, tmp_path):
    bus, store = AsyncEventBus(), InMemoryJobStore()
    await _create(store, "job-a", tmp_path)
    await _create(store, "job-b", tmp_path)
    runner = asyncio.create_task(orch.run_orchestrator(bus, store))
    try:
        await _request(bus, "job-a", tmp_path)
        await _request(bus, "job-b", tmp_path)
        await asyncio.wait_for(pipeline.entered.wait(), 5)
        await _settle()
        assert orch.job_active("job-b")

        assert orch.cancel_job("job-b") is True
        await _until(lambda: not orch.job_active("job-b"))

        job_b = await store.get("job-b")
        assert job_b.status == "cancelled"
        assert _types(bus, "job-b")[-1] is EventType.JOB_CANCELLED
        assert (await store.get("job-a")).status == "running"
        # The cancelled waiter gave no slot away and job-a still holds its own.
        assert len(pipeline.cleanup_roots) == 1
    finally:
        await _stop(runner)


async def test_shutdown_of_a_waiting_job_leaves_it_pending(pipeline, tmp_path):
    bus, store = AsyncEventBus(), InMemoryJobStore()
    await _create(store, "job-a", tmp_path)
    await _create(store, "job-b", tmp_path)
    runner = asyncio.create_task(orch.run_orchestrator(bus, store))
    await _request(bus, "job-a", tmp_path)
    await _request(bus, "job-b", tmp_path)
    await asyncio.wait_for(pipeline.entered.wait(), 5)
    await _settle()

    await asyncio.wait_for(_stop(runner), 5)

    assert (await store.get("job-b")).status == "pending"
    assert EventType.JOB_CANCELLED not in _types(bus, "job-b")


async def test_a_queued_job_marked_cancelled_never_runs(pipeline, tmp_path):
    bus, store = AsyncEventBus(), InMemoryJobStore()
    await _create(store, JOB, tmp_path)
    await store.update(JOB, lambda s: setattr(s, "status", "cancelled"))
    trigger = Event(type=EventType.VIDEO_REQUESTED, job_id=JOB, payload={"url": URL, "download_path": str(tmp_path)})

    await asyncio.wait_for(orch._run_job(trigger, bus, store), 5)

    assert (await store.get(JOB)).status == "cancelled"
    assert _types(bus, JOB) == []


def test_cancel_job_with_no_task_is_false():
    assert orch.cancel_job("nobody") is False
    assert orch._user_cancelled == set()


# ── The download thread ───────────────────────────────────────────────────────


class _SlowDownloadAdapter(_Adapter):
    """A download that, like yt-dlp's progress hook, polls ``download_stop``."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.stopped_by_hook = threading.Event()

    def download(self, url: str, destination_folder: str) -> DownloadResult:
        self.started.set()
        should_stop = download_stop.get()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if should_stop is not None and should_stop():
                self.stopped_by_hook.set()
                raise RuntimeError("The download was cancelled")
            time.sleep(0.01)
        return super().download(url, destination_folder)


async def test_user_cancel_stops_the_download_thread_and_the_job_stays_active_until_it_ends(
    pipeline, tmp_path, monkeypatch
):
    adapter = _SlowDownloadAdapter()
    monkeypatch.setattr(orch, "resolve_adapter", lambda url: adapter)
    bus, store = AsyncEventBus(), InMemoryJobStore()
    await _create(store, JOB, tmp_path)
    runner = asyncio.create_task(orch.run_orchestrator(bus, store))
    try:
        await _request(bus, JOB, tmp_path)
        await _until(adapter.started.is_set)
        await _until(lambda: JOB in orch._threads_running)

        orch.cancel_job(JOB)
        await _until(lambda: JOB not in orch._job_tasks)
        assert (await store.get(JOB)).status == "cancelled"

        await _until(lambda: not orch.job_active(JOB), timeout=3)
        assert adapter.stopped_by_hook.is_set(), "the download thread kept going after the cancel"
        assert not (tmp_path / f"vid-{JOB}" / "video.mp4").exists()
    finally:
        await _stop(runner)


def test_the_download_thread_counts_as_active_while_it_runs():
    stop = threading.Event()
    seen: list[bool] = []

    class _Probe:
        def download(self, url: str, destination: str) -> str:
            seen.append(orch.job_active(JOB))
            seen.append(download_stop.get()())
            stop.set()
            seen.append(download_stop.get()())
            return "done"

    assert orch._download_in_thread(_Probe(), URL, "/unused", JOB, stop) == "done"

    assert seen == [True, False, True]
    assert not orch.job_active(JOB)
    assert download_stop.get() is None


def test_a_download_whose_stop_is_already_set_never_starts():
    stop = threading.Event()
    stop.set()
    calls: list[str] = []

    class _Never:
        def download(self, url: str, destination: str) -> None:
            calls.append(url)

    with pytest.raises(RuntimeError):
        orch._download_in_thread(_Never(), URL, "/unused", JOB, stop)
    assert calls == []


# ── A re-render and a reprompt of a completed job ─────────────────────────────


async def _completed_job(store: InMemoryJobStore, tmp_path: Path) -> dict[str, Path]:
    clips = tmp_path / "vid" / "clips"
    clips.mkdir(parents=True)
    source = tmp_path / "vid" / "video.mp4"
    source.write_bytes(b"source")
    output = clips / "01_Outro.mp4"
    output.write_bytes(b"old")
    await store.create(JobState(job_id=JOB, url=URL, download_path=str(tmp_path)))

    def _complete(s: JobState) -> None:
        s.status = "completed"
        s.video_path = str(source)
        s.clips_folder = str(clips)
        s.output_paths = [str(output)]
        s.chapters = {1: ChapterArtifacts(chapter_index=1, status="completed")}

    await store.update(JOB, _complete)
    await store.upsert_clip(
        JOB, "old-clip", lambda c: c.update({"start": 6.0, "end": 12.0, "output_path": str(output), "title": "Outro"})
    )
    return {"source": source, "output": output}


async def _run_and_cancel(bus, store, payload, pipeline, tmp_path, *, user: bool) -> None:
    runner = asyncio.create_task(orch.run_orchestrator(bus, store))
    try:
        await _request(bus, JOB, tmp_path, **payload)
        await asyncio.wait_for(pipeline.entered.wait(), 5)
        if user:
            assert orch.cancel_job(JOB) is True
            await _until(lambda: not orch.job_active(JOB))
    finally:
        await asyncio.wait_for(_stop(runner), 5)


@pytest.mark.parametrize("user", [True, False], ids=["user", "shutdown"])
async def test_a_cancelled_rerender_puts_the_chapter_back_only_on_a_user_cancel(pipeline, tmp_path, user):
    bus, store = AsyncEventBus(), InMemoryJobStore()
    await _completed_job(store, tmp_path)

    await _run_and_cancel(bus, store, {"rerender_clip_id": "old-clip"}, pipeline, tmp_path, user=user)

    job = await store.get(JOB)
    assert job.status == "completed"
    assert job.chapters[1].status == ("completed" if user else "rendering")
    types = _types(bus, JOB)
    assert EventType.JOB_CANCELLED not in types and EventType.JOB_FAILED not in types


@pytest.mark.parametrize("user", [True, False], ids=["user", "shutdown"])
async def test_a_cancelled_reprompt_keeps_the_old_clips_and_reports_only_a_user_cancel(pipeline, tmp_path, user):
    bus, store = AsyncEventBus(), InMemoryJobStore()
    await _completed_job(store, tmp_path)
    assert orch.claim_reprompt(JOB)

    await _run_and_cancel(
        bus, store, {"reprompt": True, "start_seconds": 0.0, "end_seconds": 6.0}, pipeline, tmp_path, user=user
    )

    job = await store.get(JOB)
    assert job.status == "completed"
    assert [c["clip_id"] for c in await store.list_clips(job_id=JOB)] == ["old-clip"]
    assert not orch.reprompt_in_flight(JOB)
    failed = [e for e in bus._history if e.type is EventType.REPROMPT_FAILED]
    if user:
        assert [e.payload for e in failed] == [{"error": "cancelled", "reason": "cancelled"}]
        assert _types(bus, JOB)[-1] is EventType.REPROMPT_FAILED
    else:
        assert failed == []
    assert EventType.JOB_CANCELLED not in _types(bus, JOB)


async def test_a_reprompt_cancelled_while_waiting_releases_its_claim(pipeline, tmp_path):
    bus, store = AsyncEventBus(), InMemoryJobStore()
    await _completed_job(store, tmp_path)
    await _create(store, "job-a", tmp_path)
    runner = asyncio.create_task(orch.run_orchestrator(bus, store))
    try:
        await _request(bus, "job-a", tmp_path)
        await asyncio.wait_for(pipeline.entered.wait(), 5)
        assert orch.claim_reprompt(JOB)
        await _request(bus, JOB, tmp_path, reprompt=True, start_seconds=0.0, end_seconds=6.0)
        await _settle()

        assert orch.cancel_job(JOB) is True
        await _until(lambda: not orch.job_active(JOB))

        assert not orch.reprompt_in_flight(JOB)
        assert (await store.get(JOB)).status == "completed"
        failed = [e for e in bus._history if e.type is EventType.REPROMPT_FAILED]
        assert [e.payload["reason"] for e in failed] == ["cancelled"]
    finally:
        await _stop(runner)


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
async def test_a_cancel_that_lands_after_the_job_ended_keeps_its_outcome(status, tmp_path):
    bus, store = AsyncEventBus(), InMemoryJobStore()
    await _create(store, JOB, tmp_path)
    await store.update(JOB, lambda s: setattr(s, "status", status))

    await orch._record_cancel(bus, store, JOB)

    assert (await store.get(JOB)).status == status
    assert _types(bus, JOB) == []


class _GetBlocksStore(InMemoryJobStore):
    """``get`` of the job blocks until released: a cancel lands on the
    run's pre-start status read."""

    def __init__(self) -> None:
        super().__init__()
        self.in_get = asyncio.Event()
        self.release = asyncio.Event()

    async def get(self, job_id: str) -> JobState:
        if job_id == JOB and not self.release.is_set():
            self.in_get.set()
            await self.release.wait()
        return await super().get(job_id)


async def test_a_cancel_landing_on_the_pre_start_read_is_recorded(pipeline, tmp_path):
    bus, store = AsyncEventBus(), _GetBlocksStore()
    await _create(store, JOB, tmp_path)
    runner = asyncio.create_task(orch.run_orchestrator(bus, store))
    try:
        await _request(bus, JOB, tmp_path)
        await asyncio.wait_for(store.in_get.wait(), 5)

        assert orch.cancel_job(JOB) is True
        store.release.set()
        await _until(lambda: not orch.job_active(JOB))

        assert (await store.get(JOB)).status == "cancelled"
        assert _types(bus, JOB) == [EventType.VIDEO_REQUESTED, EventType.JOB_CANCELLED]
    finally:
        await _stop(runner)


async def test_a_worker_thread_keeps_its_job_active_after_its_task_is_cancelled():
    started, release = threading.Event(), threading.Event()

    def _export() -> str:
        started.set()
        release.wait(5)
        return "done"

    task = asyncio.create_task(orch._job_thread(JOB, _export))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert orch.job_active(JOB), "the thread still runs: the job's folder may still be written"
    release.set()
    await _until(lambda: not orch.job_active(JOB))


async def test_job_threads_are_counted_per_job_and_released_on_error():
    def _boom() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await orch._job_thread(JOB, _boom)

    assert orch._threads_running == {}


def test_a_delete_claim_is_exclusive():
    assert orch.claim_delete(JOB) is True
    assert orch.claim_delete(JOB) is False
    assert orch.deleting(JOB)
    orch.release_delete(JOB)
    assert not orch.deleting(JOB)


async def test_a_second_cancel_does_not_let_the_job_look_idle_while_its_thread_writes():
    """The auditor's PoC: a second Cancel click used to cancel the task again,
    which broke ``to_thread_cancellable``'s wait for its worker thread."""
    from app.services import ffmpeg_tools

    started, release = threading.Event(), threading.Event()

    def _slow_writer() -> None:
        started.set()
        release.wait(5)

    task = asyncio.create_task(ffmpeg_tools.to_thread_cancellable(_slow_writer))
    orch._track_job_task(JOB, task)
    await asyncio.to_thread(started.wait, 5)

    assert orch.cancel_job(JOB) is True
    await asyncio.sleep(0.05)
    assert orch.cancel_job(JOB) is True
    await asyncio.sleep(0.05)

    assert orch.job_active(JOB), "the worker thread still runs"
    assert not task.done()
    release.set()
    await _until(lambda: not orch.job_active(JOB))
    assert task.cancelled()


async def test_a_rerender_releases_its_claim_however_it_ends(tmp_path):
    """The route claims a re-render (``claim_work``) before queueing it; the
    re-render releases it when it ends, also when it fails early."""
    bus, store = AsyncEventBus(), InMemoryJobStore()
    await _create(store, JOB, tmp_path)
    orch.claim_work(JOB)
    assert orch.job_active(JOB)
    trigger = Event(type=EventType.VIDEO_REQUESTED, job_id=JOB, payload={"rerender_clip_id": "missing", "url": URL})

    await orch._run_job(trigger, bus, store)

    assert not orch.job_active(JOB)
    assert orch._work_in_flight == {}


async def test_a_rerender_cancelled_while_waiting_releases_its_claim(pipeline, tmp_path):
    bus, store = AsyncEventBus(), InMemoryJobStore()
    await _completed_job(store, tmp_path)
    await _create(store, "job-a", tmp_path)
    runner = asyncio.create_task(orch.run_orchestrator(bus, store))
    try:
        await _request(bus, "job-a", tmp_path)
        await asyncio.wait_for(pipeline.entered.wait(), 5)
        orch.claim_work(JOB)
        await _request(bus, JOB, tmp_path, rerender_clip_id="old-clip")
        await _settle()

        assert orch.cancel_job(JOB) is True
        await _until(lambda: JOB not in orch._job_tasks)

        assert not orch.job_active(JOB)
    finally:
        await _stop(runner)


def test_work_claims_are_counted():
    orch.claim_work(JOB)
    orch.claim_work(JOB)
    orch.release_work(JOB)
    assert orch.job_has_work(JOB)
    orch.release_work(JOB)
    assert not orch.job_has_work(JOB)
    orch.release_work(JOB)  # an extra release never goes negative
    assert orch._work_in_flight == {}
