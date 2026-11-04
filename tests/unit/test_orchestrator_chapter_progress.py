"""Job progress counters and download percent (T052).

The job page fills its bar from ``chapters_done / chapters_total`` and
refetches the job when ``ChapterCompleted`` arrives, so the counters must
already be in the store when each event is published: the recorder reads
the store inside ``publish``. Before chapters exist the bar shows the
download percent from ``DownloadProgress``.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import InMemoryJobStore, SqlJobStore
from app.db import models as _models  # noqa: F401 — registers tables on Base
from app.db.base import Base
from app.domain.events import Event, EventType
from app.domain.models import JobState, PipelineOptions
from app.services.platforms._yt_dlp_base import download_progress
from app.services.platforms.base import Chapter, DownloadResult
from app.services.transcription_service import WordTiming
from app.workers import orchestrator as orch

JOB_ID = "job-progress"
URL = "https://www.youtube.com/watch?v=progress0001"
SOURCE_SECONDS = 360.0
WORDS = [WordTiming(f"w{i}", float(i), i + 0.6) for i in range(360)]
THREE_CHAPTERS = [
    {"title": "A", "start": 0.0, "end": 10.0},
    {"title": "B", "start": 10.0, "end": 20.0},
    {"title": "C", "start": 20.0, "end": 30.0},
]

# See test_orchestrator_concurrency.py: 3.12's TaskGroup leaves
# cancelling()==1 after a chapter fails.
requires_py313 = pytest.mark.skipif(
    sys.version_info < (3, 13),
    reason="asyncio.TaskGroup leaves cancelling()==1 on 3.12; see PR #25",
)


@pytest.fixture(params=["memory", "sql"])
async def store(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[Any]:
    if request.param == "memory":
        yield InMemoryJobStore()
        return
    # A file database with the default pool, as in the app. With a StaticPool
    # :memory: database, a session cancelled when a sibling chapter fails
    # discards the one shared connection, and the database with it.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sql_store = SqlJobStore()
    sql_store._factory = async_sessionmaker(engine, expire_on_commit=False)
    yield sql_store
    await engine.dispose()


@pytest.fixture(autouse=True)
def _no_reprompt_in_flight():
    orch._reprompts_in_flight.clear()
    yield
    orch._reprompts_in_flight.clear()


@dataclass
class _Seen:
    type: EventType
    payload: dict[str, Any]
    done: int
    total: int


class _Recorder:
    """Records each event with the job's counters as stored when it was
    published, as the UI sees them when it refetches on that event.

    ``DownloadProgress`` is delivered slowly, the lower percents slowest: it
    is published from the download thread through the loop, and must still
    land in order and before ``VideoDownloaded``.
    """

    def __init__(self, bus: AsyncEventBus, store: Any) -> None:
        self.seen: list[_Seen] = []
        self._publish = bus.publish
        self._store = store

    async def __call__(self, event: Event) -> None:
        if event.type is EventType.DOWNLOAD_PROGRESS:
            await asyncio.sleep((100 - event.payload["percent"]) / 1000)
        job = await self._store.get(event.job_id)
        self.seen.append(_Seen(event.type, dict(event.payload), job.chapters_done, job.chapters_total))
        await self._publish(event)

    def of(self, type_: EventType) -> list[_Seen]:
        return [s for s in self.seen if s.type is type_]

    def types(self) -> list[EventType]:
        return [s.type for s in self.seen]


class _FakeAdapter:
    platform_id = "youtube"

    def __init__(self) -> None:
        self.chapters: list[dict[str, Any]] = list(THREE_CHAPTERS)
        self.fail: Exception | None = None
        self.progress: list[int] = []

    def download(self, url: str, destination_folder: str) -> DownloadResult:
        report = download_progress.get()
        if report is not None:
            for percent in self.progress:
                report(percent)
        if self.fail is not None:
            raise self.fail
        video_path = Path(destination_folder) / "source.mp4"
        video_path.write_bytes(b"source")
        info = {"title": "Talk", "duration": SOURCE_SECONDS, "chapters": self.chapters}
        return DownloadResult(
            video_path=str(video_path),
            info=info,
            title="Talk",
            duration=SOURCE_SECONDS,
            source=self.platform_id,
        )

    def extract_chapters(self, info: dict) -> list[Chapter]:
        return [
            Chapter(index=i, title=c["title"], start=c["start"], end=c["end"])
            for i, c in enumerate(info.get("chapters") or [])
        ]


class _Harness:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.base = tmp_path / "out" / "Talk"
        self.clips = self.base / "clips"
        self.safe_end = SOURCE_SECONDS
        self.adapter = _FakeAdapter()
        self.fail_render_on: set[str] = set()
        self.renders: list[str] = []

        def _subfolder(download_path, url, platform_id="video", job_id=None):
            self.clips.mkdir(parents=True, exist_ok=True)
            return str(self.base), str(self.clips)

        def _extract_audio(src, start, duration, wav_path):
            Path(wav_path).parent.mkdir(parents=True, exist_ok=True)
            Path(wav_path).write_bytes(b"\x00")
            return wav_path

        def _transcribe(audio_path, **kwargs):
            return list(WORDS)

        def _render(video_path, output_path, start, end, *args, **kwargs):
            name = Path(output_path).name
            if name in self.fail_render_on:
                raise RuntimeError(f"render exploded on {name}")
            self.renders.append(name)
            Path(output_path).write_bytes(b"reel")
            return output_path

        def _thumb(clip_path, output_path):
            Path(output_path).write_bytes(b"thumb")
            return output_path

        monkeypatch.setattr(orch.folder_service, "create_video_subfolder", _subfolder)
        monkeypatch.setattr(orch, "resolve_adapter", lambda url: self.adapter)
        monkeypatch.setattr(orch.clip_service, "probe_safe_end", lambda path: self.safe_end)
        monkeypatch.setattr(orch.clip_service, "extract_audio", _extract_audio)
        monkeypatch.setattr(orch.transcription_service, "transcribe_to_words", _transcribe)
        monkeypatch.setattr(orch.render_service, "render_clip", _render)
        monkeypatch.setattr(orch.thumbnail_service, "generate_thumbnail", _thumb)
        monkeypatch.setattr(orch.settings, "segment_provider", "local_heuristic")
        monkeypatch.setattr(orch.settings, "max_parallel_chapters", 1)
        monkeypatch.setattr(orch.settings, "ollama_enabled", False)
        monkeypatch.setattr(orch.settings, "export_base_folder", str(tmp_path / "exp"))

    async def run(self, store: Any, *, options: PipelineOptions | None = None) -> _Recorder:
        options = options or PipelineOptions(audio_enhance=False)
        await store.create(
            JobState(
                job_id=JOB_ID,
                url=URL,
                download_path=str(self.tmp_path),
                pipeline_options=options,
            )
        )
        payload = {
            "url": URL,
            "download_path": str(self.tmp_path),
            "caption_format": "srt",
            "target_aspect_ratio": 9 / 16,
            "segment_mode": "auto",
            "language": "en-US",
            "pipeline_options": options.model_dump(),
        }
        return await self._trigger(store, payload)

    async def _trigger(self, store: Any, payload: dict[str, Any]) -> _Recorder:
        bus = AsyncEventBus()
        recorder = _Recorder(bus, store)
        bus.publish = recorder  # type: ignore[method-assign]
        await orch._run_job(
            Event(type=EventType.VIDEO_REQUESTED, job_id=JOB_ID, payload=payload),
            bus,
            store,
        )
        return recorder

    async def rerender(self, store: Any, clip_id: str) -> _Recorder:
        return await self._trigger(store, {"rerender_clip_id": clip_id})

    async def reprompt(self, store: Any) -> _Recorder:
        return await self._trigger(store, {"reprompt": True, "start_seconds": 40.0, "end_seconds": 70.0})


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    return _Harness(tmp_path, monkeypatch)


async def _counters(store: Any) -> tuple[int, int]:
    job = await store.get(JOB_ID)
    return job.chapters_done, job.chapters_total


# ── chapter counters ──────────────────────────────────────────────────────────


async def test_total_is_stored_before_chapters_detected(harness, store):
    recorder = await harness.run(store)

    (detected,) = recorder.of(EventType.CHAPTERS_DETECTED)
    assert (detected.done, detected.total) == (0, 3)


async def test_done_is_stored_before_each_chapter_completed(harness, store):
    recorder = await harness.run(store)

    completed = recorder.of(EventType.CHAPTER_COMPLETED)
    assert [(s.done, s.total) for s in completed] == [(1, 3), (2, 3), (3, 3)]
    assert [s.payload for s in completed] == [
        {"chapter_index": 0},
        {"chapter_index": 1},
        {"chapter_index": 2},
    ]
    assert (await store.get(JOB_ID)).status == "completed"
    assert await _counters(store) == (3, 3)


async def test_chapter_completed_follows_chapters_detected(harness, store):
    recorder = await harness.run(store)

    types = recorder.types()
    first_completed = types.index(EventType.CHAPTER_COMPLETED)
    assert types.index(EventType.CHAPTERS_DETECTED) < first_completed
    assert first_completed < types.index(EventType.JOB_COMPLETED)


async def test_total_counts_chapters_left_after_the_clamp(harness, store):
    harness.safe_end = 20.2  # chapter C becomes 0.2 s long and is dropped

    recorder = await harness.run(store)

    (detected,) = recorder.of(EventType.CHAPTERS_DETECTED)
    assert len(detected.payload["chapters"]) == 2
    assert detected.total == 2
    assert await _counters(store) == (2, 2)


async def test_source_without_chapters_counts_one_full_video(harness, store):
    harness.adapter.chapters = []

    recorder = await harness.run(store, options=PipelineOptions(audio_enhance=False, segment_proposer=False))

    (detected,) = recorder.of(EventType.CHAPTERS_DETECTED)
    assert [c["title"] for c in detected.payload["chapters"]] == ["Full Video"]
    assert detected.total == 1
    assert await _counters(store) == (1, 1)


async def test_chapters_count_when_render_is_off(harness, store):
    recorder = await harness.run(store, options=PipelineOptions(audio_enhance=False, render=False))

    assert harness.renders == []
    assert len(recorder.of(EventType.CHAPTER_COMPLETED)) == 3
    assert await _counters(store) == (3, 3)


async def test_download_failure_leaves_counters_at_zero(harness, store):
    harness.adapter.fail = RuntimeError("geo-blocked")

    recorder = await harness.run(store)

    assert (await store.get(JOB_ID)).status == "failed"
    assert await _counters(store) == (0, 0)
    assert recorder.of(EventType.CHAPTER_COMPLETED) == []


@requires_py313
async def test_failed_chapter_is_not_counted(harness, store):
    harness.fail_render_on = {"01_B.mp4"}

    recorder = await harness.run(store)

    assert (await store.get(JOB_ID)).status == "failed"
    assert await _counters(store) == (1, 3)
    assert [s.payload for s in recorder.of(EventType.CHAPTER_COMPLETED)] == [{"chapter_index": 0}]
    assert recorder.types()[-1] is EventType.JOB_FAILED


async def test_rerender_leaves_counters_unchanged(harness, store):
    await harness.run(store)
    clip = next(c for c in await store.list_clips(job_id=JOB_ID) if Path(c["output_path"]).name == "01_B.mp4")
    harness.renders.clear()

    recorder = await harness.rerender(store, clip["clip_id"])

    assert harness.renders == ["01_B.mp4"]
    assert recorder.of(EventType.CHAPTER_COMPLETED) == []
    assert await _counters(store) == (3, 3)


async def test_reprompt_leaves_counters_unchanged(harness, store):
    await harness.run(store)
    harness.renders.clear()

    recorder = await harness.reprompt(store)

    assert EventType.JOB_REPROMPTED in recorder.types()
    assert len(harness.renders) == 1
    assert recorder.of(EventType.CHAPTER_COMPLETED) == []
    assert await _counters(store) == (3, 3)


# ── download percent ──────────────────────────────────────────────────────────


async def test_download_progress_is_emitted_before_video_downloaded(harness, store):
    harness.adapter.progress = [10, 50, 100]

    recorder = await harness.run(store)

    assert [s.payload for s in recorder.of(EventType.DOWNLOAD_PROGRESS)] == [
        {"percent": 10},
        {"percent": 50},
        {"percent": 100},
    ]
    types = recorder.types()
    last_progress = len(types) - 1 - types[::-1].index(EventType.DOWNLOAD_PROGRESS)
    assert last_progress < types.index(EventType.VIDEO_DOWNLOADED)


async def test_download_progress_callback_is_cleared_after_the_download(harness, store):
    await harness.run(store)

    assert download_progress.get() is None


async def test_a_percent_reported_after_the_download_block_is_dropped():
    """A download thread that outlived its timeout may still report; nothing
    is published after the block, and the report does not raise into it."""
    bus = AsyncEventBus()
    published: list[Event] = []

    async def _publish(event: Event) -> None:
        published.append(event)

    bus.publish = _publish  # type: ignore[method-assign]
    async with orch._download_progress_events(bus, JOB_ID):
        report = download_progress.get()
        await asyncio.to_thread(report, 40)

    await asyncio.to_thread(report, 60)
    await asyncio.sleep(0.01)

    assert [e.payload for e in published] == [{"percent": 40}]
    assert download_progress.get() is None
