"""Orchestrator concurrency: the job cap and the per-chapter fan-out.

* ``max_concurrent_jobs`` is enforced inside ``run_orchestrator`` (a job waits
  for a slot before ``_run_job`` starts), not around the queue's ``publish()``.
* A failing chapter cancels its siblings: their ffmpeg is killed, their worker
  threads have finished before the job's tmp dir is removed, ``JobFailed`` is
  emitted exactly once and nothing is emitted after it.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import JobStore
from app.domain.events import Event, EventType
from app.domain.models import JobState, PipelineOptions
from app.services import ffmpeg_tools
from app.services.platforms.base import Chapter, DownloadResult
from app.services.transcription_service import WordTiming
from app.workers import orchestrator as orch

URL = "https://www.youtube.com/watch?v=fake"

# 3.12's TaskGroup leaves the parent task's cancelling() at 1 after a child
# fails (fixed in 3.13), so _finish_then_honour_cancel re-raises a cancel
# nobody sent. CI and the docs are 3.14 only.
requires_py313 = pytest.mark.skipif(
    sys.version_info < (3, 13),
    reason="asyncio.TaskGroup leaves cancelling()==1 on 3.12; see PR #25",
)


# ── Job cap ───────────────────────────────────────────────────────────────────


class _JobGate:
    """Stand-in for ``_run_job``: records starts and blocks until released."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.finished: list[str] = []
        self.release: dict[str, asyncio.Event] = {}

    async def run(self, trigger: Event, bus: AsyncEventBus, store: Any) -> None:
        self.started.append(trigger.job_id)
        gate = self.release.setdefault(trigger.job_id, asyncio.Event())
        await gate.wait()
        self.finished.append(trigger.job_id)


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)
    await asyncio.sleep(0.02)


async def _request(bus: AsyncEventBus, job_id: str) -> None:
    await bus.publish(
        Event(
            type=EventType.VIDEO_REQUESTED,
            job_id=job_id,
            payload={"url": URL, "download_path": "/tmp/unused"},
        )
    )


async def _stop(task: asyncio.Task[Any]) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_job_cap_of_one_runs_second_job_only_after_first_finishes(monkeypatch):
    gate = _JobGate()
    monkeypatch.setattr(orch, "_run_job", gate.run)
    monkeypatch.setattr(orch.settings, "max_concurrent_jobs", 1)
    bus = AsyncEventBus()
    runner = asyncio.create_task(orch.run_orchestrator(bus, JobStore()))
    try:
        await _request(bus, "job-a")
        await _request(bus, "job-b")
        await _settle()
        assert gate.started == ["job-a"], "second job must wait for a free slot"

        gate.release["job-a"].set()
        await _settle()
        assert gate.finished == ["job-a"]
        assert gate.started == ["job-a", "job-b"]

        gate.release["job-b"].set()
        await _settle()
        assert gate.finished == ["job-a", "job-b"]
    finally:
        await _stop(runner)


async def test_job_cap_of_two_runs_both_jobs_at_once(monkeypatch):
    gate = _JobGate()
    monkeypatch.setattr(orch, "_run_job", gate.run)
    monkeypatch.setattr(orch.settings, "max_concurrent_jobs", 2)
    bus = AsyncEventBus()
    runner = asyncio.create_task(orch.run_orchestrator(bus, JobStore()))
    try:
        await _request(bus, "job-a")
        await _request(bus, "job-b")
        await _request(bus, "job-c")
        await _settle()
        assert gate.started == ["job-a", "job-b"]
        gate.release["job-b"].set()
        await _settle()
        assert gate.started == ["job-a", "job-b", "job-c"]
    finally:
        for gate_event in gate.release.values():
            gate_event.set()
        await _stop(runner)


async def test_shutdown_cancels_running_and_waiting_jobs(monkeypatch):
    gate = _JobGate()
    monkeypatch.setattr(orch, "_run_job", gate.run)
    monkeypatch.setattr(orch.settings, "max_concurrent_jobs", 1)
    bus = AsyncEventBus()
    runner = asyncio.create_task(orch.run_orchestrator(bus, JobStore()))
    await _request(bus, "job-a")
    await _request(bus, "job-b")
    await _settle()

    await asyncio.wait_for(_stop(runner), timeout=2)

    assert gate.started == ["job-a"]
    assert gate.finished == []


# ── Chapter fan-out ───────────────────────────────────────────────────────────

JOB_ID = "job-fanout"
# Long enough that a leaked sibling would still be alive when we look.
_SIBLING_SLEEP_S = 20


class _TwoChapterAdapter:
    platform_id = "youtube"

    def download(self, url: str, destination_folder: str) -> DownloadResult:
        video_path = str(Path(destination_folder) / "video.mp4")
        Path(video_path).write_bytes(b"\x00")
        return DownloadResult(
            video_path=video_path,
            info={"title": "T", "duration": 12.0},
            title="T",
            duration=12.0,
            source=self.platform_id,
        )

    def extract_chapters(self, info: dict[str, Any]) -> list[Chapter]:
        return [
            Chapter(index=0, title="Fails", start=0.0, end=6.0),
            Chapter(index=1, title="Sibling", start=6.0, end=12.0),
        ]


def _fake_subfolder(download_path: str, url: str, platform_id: str = "video"):
    clips = Path(download_path) / "vid" / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    return str(clips.parent), str(clips)


def _fake_extract_audio(src: str, start: float, duration: float, wav_path: str) -> str:
    Path(wav_path).write_bytes(b"\x00")
    return wav_path


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for_file(path: Path, timeout_s: float = 10) -> None:
    deadline = time.monotonic() + timeout_s
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"{path} never appeared")
        time.sleep(0.01)


@pytest.fixture
def fanout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(orch.settings, "export_base_folder", str(tmp_path / "exports"))
    monkeypatch.setattr(orch.settings, "max_parallel_chapters", 2)
    monkeypatch.setattr(orch.folder_service, "create_video_subfolder", _fake_subfolder)
    monkeypatch.setattr(orch, "resolve_adapter", lambda url: _TwoChapterAdapter())
    monkeypatch.setattr(orch.clip_service, "probe_safe_end", lambda path: 999.0)
    monkeypatch.setattr(orch.clip_service, "extract_audio", _fake_extract_audio)
    monkeypatch.setattr(
        orch.transcription_service,
        "transcribe_to_words",
        lambda audio_path, **_: [WordTiming("hi", 0.0, 0.5)],
    )
    return tmp_path


async def _run_one_job(
    tmp_path: Path, options: PipelineOptions
) -> tuple[AsyncEventBus, JobStore]:
    bus = AsyncEventBus()
    store = JobStore()
    await store.create(
        JobState(job_id=JOB_ID, url=URL, source="youtube", download_path=str(tmp_path))
    )
    trigger = Event(
        type=EventType.VIDEO_REQUESTED,
        job_id=JOB_ID,
        payload={
            "url": URL,
            "download_path": str(tmp_path),
            "pipeline_options": options.model_dump(),
        },
    )
    await asyncio.wait_for(orch._run_job(trigger, bus, store), timeout=15)
    return bus, store


def _job_events(bus: AsyncEventBus) -> list[Event]:
    return [e for e in bus._history if e.job_id == JOB_ID]


@requires_py313
async def test_failing_chapter_kills_sibling_ffmpeg_and_fails_job_once(
    fanout: Path, monkeypatch: pytest.MonkeyPatch
):
    pid_file = fanout / "sibling.pid"
    tmp_dirs_seen: list[Path] = []

    def fake_render(video_path, output_path, start, end, captions_path, *a, **kw):
        if Path(output_path).name.startswith("01_"):
            # Sibling: a long-running child under ffmpeg_tools.run, which is
            # what render_clip uses. Only its kill ends it early.
            ffmpeg_tools.run(
                [
                    sys.executable,
                    "-c",
                    "import os, sys, time; "
                    "open(sys.argv[1], 'w').write(str(os.getpid())); "
                    f"time.sleep({_SIBLING_SLEEP_S})",
                    str(pid_file),
                ]
            )
            return output_path
        tmp_dirs_seen.append(Path(captions_path).parent)
        _wait_for_file(pid_file)
        time.sleep(0.05)  # let the child finish writing its pid
        raise RuntimeError("chapter 0 exploded")

    monkeypatch.setattr(orch.render_service, "render_clip", fake_render)

    t0 = time.monotonic()
    bus, store = await _run_one_job(fanout, PipelineOptions(thumbnail=False, audio_enhance=False))
    elapsed = time.monotonic() - t0

    sibling_pid = int(pid_file.read_text())
    assert not _pid_alive(sibling_pid), "sibling ffmpeg left running after job failed"
    assert elapsed < _SIBLING_SLEEP_S / 2

    types = [e.type for e in _job_events(bus)]
    assert types.count(EventType.JOB_FAILED) == 1
    assert types[-1] is EventType.JOB_FAILED
    assert EventType.CLIP_RENDERED not in types
    failed = next(e for e in _job_events(bus) if e.type is EventType.JOB_FAILED)
    assert failed.payload["error"] == "chapter 0 exploded"

    state = await store.get(JOB_ID)
    assert state.status == "failed"
    assert state.error == "chapter 0 exploded"
    assert tmp_dirs_seen and not tmp_dirs_seen[0].exists()

    # Nothing more is published once the job has reported failure.
    count = len(_job_events(bus))
    await asyncio.sleep(0.3)
    assert len(_job_events(bus)) == count


@requires_py313
async def test_sibling_worker_thread_finishes_before_tmp_dir_cleanup(
    fanout: Path, monkeypatch: pytest.MonkeyPatch
):
    """A sibling writing into the job tmp dir must not race its removal."""
    writer_started = threading.Event()
    writer_done = threading.Event()
    real_write = orch.caption_service.write_captions

    def slow_write(captions, fmt, path):
        if Path(path).name.startswith("chapter_1"):
            writer_started.set()
            time.sleep(0.4)
            real_write(captions, fmt, path)
            writer_done.set()
            return
        real_write(captions, fmt, path)

    def fake_render(video_path, output_path, *a, **kw):
        if Path(output_path).name.startswith("00_"):
            writer_started.wait(5)
            raise RuntimeError("chapter 0 exploded")
        return output_path

    monkeypatch.setattr(orch.caption_service, "write_captions", slow_write)
    monkeypatch.setattr(orch.render_service, "render_clip", fake_render)

    bus, store = await _run_one_job(fanout, PipelineOptions(thumbnail=False, audio_enhance=False))

    assert writer_done.is_set(), (
        "job finished while a sibling thread still wrote to tmp"
    )
    assert (await store.get(JOB_ID)).status == "failed"
    assert [e.type for e in _job_events(bus)].count(EventType.JOB_FAILED) == 1


async def test_two_chapters_still_complete_with_parallel_fan_out(
    fanout: Path, monkeypatch: pytest.MonkeyPatch
):
    def fake_render(video_path, output_path, *a, **kw):
        Path(output_path).write_bytes(b"\x00")
        return output_path

    monkeypatch.setattr(orch.render_service, "render_clip", fake_render)

    bus, store = await _run_one_job(fanout, PipelineOptions(thumbnail=False, audio_enhance=False))

    types = [e.type for e in _job_events(bus)]
    assert types.count(EventType.CLIP_RENDERED) == 2
    assert types[-1] is EventType.JOB_COMPLETED
    state = await store.get(JOB_ID)
    assert state.status == "completed"
    assert sorted(Path(p).name[:2] for p in state.output_paths) == ["00", "01"]


@requires_py313
async def test_sequential_chapters_stop_at_first_failure(
    fanout: Path, monkeypatch: pytest.MonkeyPatch
):
    """Default max_parallel_chapters=1: a queued chapter must not start (and
    emit events) after an earlier chapter has failed the job."""
    monkeypatch.setattr(orch.settings, "max_parallel_chapters", 1)
    rendered: list[str] = []

    def fake_render(video_path, output_path, *a, **kw):
        rendered.append(Path(output_path).name[:2])
        if Path(output_path).name.startswith("00_"):
            raise RuntimeError("chapter 0 exploded")
        return output_path

    monkeypatch.setattr(orch.render_service, "render_clip", fake_render)

    bus, store = await _run_one_job(
        fanout, PipelineOptions(thumbnail=False, audio_enhance=False)
    )
    count = len(_job_events(bus))
    await asyncio.sleep(0.3)

    assert rendered == ["00"]
    types = [e.type for e in _job_events(bus)]
    assert len(types) == count
    assert types[-1] is EventType.JOB_FAILED
    assert types.count(EventType.JOB_FAILED) == 1
    assert (await store.get(JOB_ID)).error == "chapter 0 exploded"


async def test_shutdown_waits_for_every_job_to_unwind(monkeypatch):
    """Cancelling the orchestrator must wait for *all* jobs' cancellation
    cleanup (that is where their ffmpeg gets killed), not just the first.

    Jobs unwind at different speeds and the orchestrator holds them in a set,
    so the scenario is repeated: a version that stops waiting after the first
    cancelled job fails each round with probability 2/3 (~99.9% over 6).
    """
    monkeypatch.setattr(orch.settings, "max_concurrent_jobs", 3)
    for _round in range(6):
        unwound: list[str] = []

        async def slow_unwind(trigger: Event, bus: AsyncEventBus, store: Any) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(int(trigger.job_id[-1]) * 0.05)
                unwound.append(trigger.job_id)
                raise

        monkeypatch.setattr(orch, "_run_job", slow_unwind)
        bus = AsyncEventBus()
        runner = asyncio.create_task(orch.run_orchestrator(bus, JobStore()))
        for i in range(3):
            await _request(bus, f"job-{i}")
        await _settle()

        await asyncio.wait_for(_stop(runner), timeout=2)

        assert sorted(unwound) == ["job-0", "job-1", "job-2"]


# ── Failure bookkeeping under cancellation / multiple failures ───────────────


class _YieldingStore(JobStore):
    """In-memory store whose writes suspend like a real DB round-trip, so a
    pending cancellation is delivered inside ``update`` (as with SqlJobStore)."""

    async def update(self, job_id, mutator):
        await asyncio.sleep(0)
        return await super().update(job_id, mutator)


def _fake_chapters(*, fail_after_s: float = 0.0, sibling_unwind_s: float = 0.0):
    """Stand-in ``_process_chapter``: chapter 0 fails, chapter 1 blocks and,
    when cancelled, takes ``sibling_unwind_s`` to unwind (like killing ffmpeg)."""
    chapter0_failed = asyncio.Event()

    async def fake(*, chapter, **_kw):
        index = int(chapter["index"])
        if index == 0:
            await asyncio.sleep(fail_after_s)
            chapter0_failed.set()
            raise RuntimeError("chapter 0 broke")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(sibling_unwind_s)
            raise
        return None

    return fake, chapter0_failed


async def _start_job(fanout_dir: Path, store: JobStore) -> tuple[AsyncEventBus, asyncio.Task]:
    bus = AsyncEventBus()
    await store.create(
        JobState(job_id=JOB_ID, url=URL, source="youtube", download_path=str(fanout_dir))
    )
    trigger = Event(
        type=EventType.VIDEO_REQUESTED,
        job_id=JOB_ID,
        payload={"url": URL, "download_path": str(fanout_dir)},
    )
    return bus, asyncio.create_task(orch._run_job(trigger, bus, store))


async def test_outer_cancel_during_chapter_failure_still_marks_job_failed(
    fanout: Path, monkeypatch: pytest.MonkeyPatch
):
    fake, chapter0_failed = _fake_chapters(sibling_unwind_s=0.2)
    monkeypatch.setattr(orch, "_process_chapter", fake)
    store = _YieldingStore()
    bus, job = await _start_job(fanout, store)

    await asyncio.wait_for(chapter0_failed.wait(), timeout=5)
    await asyncio.sleep(0.05)  # the group is now cancelling chapter 1
    job.cancel()  # e.g. app shutdown

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job, timeout=5)

    state = await store.get(JOB_ID)
    assert (state.status, state.error) == ("failed", "chapter 0 broke")
    types = [e.type for e in _job_events(bus)]
    assert types.count(EventType.JOB_FAILED) == 1
    assert types[-1] is EventType.JOB_FAILED


@requires_py313
async def test_other_chapter_failures_are_logged(
    fanout: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    async def both_fail(*, chapter, **_kw):
        await asyncio.sleep(0)
        raise RuntimeError(f"chapter {chapter['index']} broke")

    monkeypatch.setattr(orch, "_process_chapter", both_fail)
    store = JobStore()
    _bus, job = await _start_job(fanout, store)

    with caplog.at_level("WARNING", logger=orch.log.name):
        await asyncio.wait_for(job, timeout=5)

    assert (await store.get(JOB_ID)).error == "chapter 0 broke"
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("chapter 1 broke" in r.getMessage() for r in warnings)


async def test_job_cap_below_one_is_clamped_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    gate = _JobGate()
    monkeypatch.setattr(orch, "_run_job", gate.run)
    monkeypatch.setattr(orch.settings, "max_concurrent_jobs", 0)
    bus = AsyncEventBus()
    with caplog.at_level("WARNING", logger=orch.log.name):
        runner = asyncio.create_task(orch.run_orchestrator(bus, JobStore()))
        try:
            await _request(bus, "job-a")
            await _request(bus, "job-b")
            await _settle()
            assert gate.started == ["job-a"]
        finally:
            await _stop(runner)

    assert any(
        "max_concurrent_jobs" in r.getMessage() and r.levelname == "WARNING"
        for r in caplog.records
    )
