"""Reprompt: re-discover the clips of a completed job from its retained source
(FR-016, T008).

``POST /jobs/{id}/reprompt`` queues a ``{"reprompt": True, ...}`` payload that
``_run_job`` hands to ``_reprompt_job``. The job stays ``completed`` the whole
time; the new clips are rendered first (hidden until all of them succeed),
then the old clips are retired and ``JobReprompted`` + ``JobCompleted`` are
emitted. Any failure keeps the old clips, hides the new ones and emits
``RepromptFailed``, never ``JobFailed``.
"""

from __future__ import annotations

import asyncio
import csv
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import InMemoryJobStore, SqlJobStore
from app.db import models as _models  # noqa: F401 — registers tables on Base
from app.db.base import Base
from app.domain.events import Event, EventType
from app.domain.models import JobState, PipelineOptions
from app.routers import jobs as jobs_router
from app.services import segment_discovery
from app.services.platforms.base import DownloadResult
from app.services.segment_proposer import ProposedSegment
from app.services.transcription_service import WordTiming
from app.workers import orchestrator as orch

JOB_ID = "job-reprompt"
URL = "https://www.youtube.com/watch?v=reprompt0001"
# 6 minutes: a clip budget of 3 and a 180 s coverage cap.
SOURCE_SECONDS = 360.0
SOURCE_WORDS = [WordTiming(f"w{i}", float(i), i + 0.6) for i in range(360)]
PROMPT = "gender equality"

# What the proposer returns for the original run, then for the reprompt. The
# reprompt reuses the title "Alpha": with clip indexes restarting at 0 its
# file would overwrite the old Alpha clip's file.
ORIGINAL = [
    ProposedSegment(start=10.3, end=39.3, title="Alpha", score=30),
    ProposedSegment(start=50.0, end=80.0, title="Beta", score=20),
]
REPROMPTED = [
    ProposedSegment(start=200.0, end=230.0, title="Alpha", score=40),
    ProposedSegment(start=250.0, end=280.0, title="Gamma", score=30),
]


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(params=["memory", "sql"])
async def store(request: pytest.FixtureRequest) -> AsyncIterator[Any]:
    if request.param == "memory":
        yield InMemoryJobStore()
        return
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sql_store = SqlJobStore()
    sql_store._factory = async_sessionmaker(engine, expire_on_commit=False)
    yield sql_store
    await engine.dispose()


@pytest.fixture
def memory_store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture(autouse=True)
def _no_reprompt_in_flight():
    orch._reprompts_in_flight.clear()
    yield
    orch._reprompts_in_flight.clear()


class _Recorder:
    def __init__(self, bus: AsyncEventBus) -> None:
        self.events: list[Event] = []
        self._publish = bus.publish

    async def __call__(self, event: Event) -> None:
        self.events.append(event)
        await self._publish(event)

    def of(self, type_: EventType) -> list[Event]:
        return [e for e in self.events if e.type is type_]

    def types(self) -> list[EventType]:
        return [e.type for e in self.events]


class _FakeAdapter:
    platform_id = "youtube"

    def download(self, url: str, destination_folder: str) -> DownloadResult:
        video_path = Path(destination_folder) / "source.mp4"
        video_path.write_bytes(b"source")
        info = {"title": "Talk", "duration": SOURCE_SECONDS, "chapters": []}
        return DownloadResult(
            video_path=str(video_path),
            info=info,
            title="Talk",
            duration=SOURCE_SECONDS,
            source=self.platform_id,
        )

    def extract_chapters(self, info: dict) -> list:
        return []


class _FakeProposer:
    """Returns the next queued result per ``propose`` call and records the call."""

    def __init__(self, results: list[list[ProposedSegment]]) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []

    def propose(self, word_timings, audio_path, chapters, duration, *, prompt=None):
        self.calls.append(
            {
                "words": list(word_timings),
                "audio_existed": bool(audio_path) and Path(audio_path).exists(),
                "duration": duration,
                "prompt": prompt,
            }
        )
        return list(self.results.pop(0))


class _Harness:
    """Fakes every service around the pipeline and records what reached them."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.base = tmp_path / "out" / "Talk-job"
        self.clips = self.base / "clips"
        self.transcribe_calls: list[str] = []
        self.renders: list[str] = []
        self.fail_on: set[str] = set()
        # (file name, started, release): that render waits until released.
        self.block: tuple[str, threading.Event, threading.Event] | None = None
        self.proposer = _FakeProposer([list(ORIGINAL), list(REPROMPTED)])
        self.factory_kwargs: list[dict[str, Any]] = []
        # One bus for the original run and the reprompt, as in the app.
        self.bus = AsyncEventBus()
        self.recorder = _Recorder(self.bus)
        self.bus.publish = self.recorder  # type: ignore[method-assign]

        def _subfolder(download_path, url, platform_id="video", job_id=None):
            self.clips.mkdir(parents=True, exist_ok=True)
            return str(self.base), str(self.clips)

        def _extract_audio(src, start, duration, wav_path):
            Path(wav_path).parent.mkdir(parents=True, exist_ok=True)
            Path(wav_path).write_bytes(b"\x00")
            return wav_path

        def _transcribe(audio_path, **_):
            self.transcribe_calls.append(audio_path)
            return list(SOURCE_WORDS)

        def _render(video_path, output_path, start, end, *args, **kwargs):
            name = Path(output_path).name
            if self.block is not None and self.block[0] == name:
                _, started, release = self.block
                started.set()
                release.wait(5)
            if name in self.fail_on:
                raise RuntimeError(f"render exploded on {name}")
            self.renders.append(output_path)
            Path(output_path).write_bytes(f"reel {start}-{end}".encode())
            return output_path

        def _thumb(clip_path, output_path):
            Path(output_path).write_bytes(b"thumb")
            return output_path

        def _factory(**kwargs):
            self.factory_kwargs.append(kwargs)
            return self.proposer

        monkeypatch.setattr(orch.folder_service, "create_video_subfolder", _subfolder)
        monkeypatch.setattr(orch, "resolve_adapter", lambda url: _FakeAdapter())
        monkeypatch.setattr(
            orch.clip_service, "probe_safe_end", lambda path: SOURCE_SECONDS
        )
        monkeypatch.setattr(orch.clip_service, "extract_audio", _extract_audio)
        monkeypatch.setattr(
            orch.transcription_service, "transcribe_to_words", _transcribe
        )
        monkeypatch.setattr(orch.render_service, "render_clip", _render)
        monkeypatch.setattr(orch.thumbnail_service, "generate_thumbnail", _thumb)
        monkeypatch.setattr(orch.segment_proposer, "get_segment_proposer", _factory)
        monkeypatch.setattr(orch.settings, "segment_provider", "local_heuristic")
        monkeypatch.setattr(orch.settings, "max_parallel_chapters", 1)
        monkeypatch.setattr(orch.settings, "ollama_enabled", False)
        monkeypatch.setattr(orch.settings, "export_base_folder", str(tmp_path / "exp"))

    @property
    def source(self) -> Path:
        return self.base / "source.mp4"

    @property
    def export_dir(self) -> Path:
        return self.tmp_path / "exp" / JOB_ID

    async def complete_job(self, store: Any) -> list[dict[str, Any]]:
        """Run the original job (two discovered clips); return its clips."""
        options = PipelineOptions(audio_enhance=False)
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
            "prompt": None,
            "pipeline_options": options.model_dump(),
        }
        await orch._run_job(
            Event(type=EventType.VIDEO_REQUESTED, job_id=JOB_ID, payload=payload),
            self.bus,
            store,
        )
        job = await store.get(JOB_ID)
        assert job.status == "completed", job.error
        self.renders.clear()
        return await _live(store)

    async def reprompt(self, store: Any, **payload: Any) -> _Recorder:
        trigger_payload = {"reprompt": True, "prompt": PROMPT, **payload}
        self.recorder.events.clear()
        await orch._run_job(
            Event(
                type=EventType.VIDEO_REQUESTED, job_id=JOB_ID, payload=trigger_payload
            ),
            self.bus,
            store,
        )
        return self.recorder


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    return _Harness(tmp_path, monkeypatch)


async def _live(store: Any) -> list[dict[str, Any]]:
    return sorted(await store.list_clips(job_id=JOB_ID), key=lambda c: c["start"])


def _names(clips: list[dict[str, Any]]) -> list[str]:
    return [Path(c["output_path"]).name for c in clips]


# ── happy path ────────────────────────────────────────────────────────────────


async def test_reprompt_replaces_the_clips_and_keeps_the_job_completed(harness, store):
    old = await harness.complete_job(store)
    assert _names(old) == ["00_Alpha.mp4", "01_Beta.mp4"]

    recorder = await harness.reprompt(store)

    new = await _live(store)
    assert _names(new) == ["02_Alpha.mp4", "03_Gamma.mp4"]
    assert [(c["start"], c["end"]) for c in new] == [(200.0, 230.0), (250.0, 280.0)]
    for clip in old:
        retired = await store.get_clip(clip["clip_id"], include_retired=True)
        assert retired["retired"] is True
    job = await store.get(JOB_ID)
    assert job.status == "completed"
    assert job.error is None
    assert sorted(job.output_paths) == sorted(c["output_path"] for c in new)
    types = recorder.types()
    assert types[-2:] == [EventType.JOB_REPROMPTED, EventType.JOB_COMPLETED]
    assert EventType.JOB_FAILED not in types
    assert EventType.REPROMPT_FAILED not in types
    (reprompted,) = recorder.of(EventType.JOB_REPROMPTED)
    assert reprompted.payload["prompt"] == PROMPT
    assert reprompted.payload["clip_ids"] == [c["clip_id"] for c in new]
    assert sorted(reprompted.payload["retired_clip_ids"]) == sorted(
        c["clip_id"] for c in old
    )


async def test_reprompt_retires_old_clips_only_after_every_new_clip_rendered(
    harness, store
):
    old = await harness.complete_job(store)
    old_ids = sorted(c["clip_id"] for c in old)
    retire_calls: list[dict[str, Any]] = []
    real_retire = store.retire_clips

    async def _spy(job_id, clip_ids):
        retire_calls.append(
            {
                "ids": sorted(clip_ids),
                "renders": list(harness.renders),
                "live": _names(await _live(store)),
            }
        )
        return await real_retire(job_id, clip_ids)

    store.retire_clips = _spy

    await harness.reprompt(store)

    assert retire_calls == [
        {
            "ids": old_ids,
            "renders": [
                str(harness.clips / "02_Alpha.mp4"),
                str(harness.clips / "03_Gamma.mp4"),
            ],
            # The new clips went live before the old ones were retired.
            "live": ["00_Alpha.mp4", "01_Beta.mp4", "02_Alpha.mp4", "03_Gamma.mp4"],
        }
    ]


async def test_reprompt_clip_indexes_follow_the_existing_ones(harness, memory_store):
    old = await harness.complete_job(memory_store)
    old_bytes = {c["output_path"]: Path(c["output_path"]).read_bytes() for c in old}

    await harness.reprompt(memory_store)

    for path, data in old_bytes.items():
        assert Path(path).read_bytes() == data
    assert _names(await _live(memory_store)) == ["02_Alpha.mp4", "03_Gamma.mp4"]
    job = await memory_store.get(JOB_ID)
    assert sorted(job.chapters) == [2, 3]


async def test_reprompt_passes_the_prompt_and_lengths_to_the_proposer(harness, store):
    await harness.complete_job(store)
    original = (await store.get(JOB_ID)).pipeline_options

    await harness.reprompt(
        store, target_length_min_seconds=25, target_length_max_seconds=45
    )

    assert harness.proposer.calls[-1]["prompt"] == PROMPT
    assert harness.proposer.calls[-1]["words"] == SOURCE_WORDS
    assert harness.proposer.calls[-1]["audio_existed"] is True
    assert harness.factory_kwargs[-1] == {"min_secs": 25, "max_secs": 45}
    job = await store.get(JOB_ID)
    assert job.prompt == PROMPT
    # Only the prompt and the length range are recorded (T028 a2).
    assert job.pipeline_options.model_dump() == {
        **original.model_dump(),
        "target_length_min_seconds": 25,
        "target_length_max_seconds": 45,
    }


async def test_reprompt_reuses_the_words_sidecar(harness, memory_store):
    await harness.complete_job(memory_store)
    assert len(harness.transcribe_calls) == 1

    await harness.reprompt(memory_store)

    assert len(harness.transcribe_calls) == 1


async def test_reprompt_transcribes_once_and_saves_the_sidecar_when_missing(
    harness, memory_store
):
    await harness.complete_job(memory_store)
    sidecar = segment_discovery.words_sidecar_path(str(harness.source))
    sidecar.unlink()

    await harness.reprompt(memory_store)

    assert len(harness.transcribe_calls) == 2
    assert segment_discovery.read_words_sidecar(str(harness.source)) == SOURCE_WORDS
    assert _names(await _live(memory_store)) == ["02_Alpha.mp4", "03_Gamma.mp4"]


async def test_reprompt_with_an_explicit_range_makes_one_clip(harness, store):
    old = await harness.complete_job(store)

    recorder = await harness.reprompt(store, start_seconds=100.0, end_seconds=130.5)

    assert len(harness.proposer.calls) == 1  # the original run only
    (clip,) = await _live(store)
    assert (clip["start"], clip["end"]) == (100.0, 130.5)
    assert clip["title"] == "Clip 1:40-2:10"
    assert Path(clip["output_path"]).name.startswith("02_")
    for c in old:
        assert (await store.get_clip(c["clip_id"], include_retired=True))["retired"]
    assert recorder.types()[-1] is EventType.JOB_COMPLETED


async def test_reprompt_rewrites_the_manifest_with_the_new_clips(harness, memory_store):
    await harness.complete_job(memory_store)

    await harness.reprompt(memory_store)

    with (harness.export_dir / "manifest.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert sorted(r["filename"] for r in rows) == ["02_Alpha.mp4", "03_Gamma.mp4"]
    assert (harness.export_dir / "02_Alpha.mp4").is_file()


async def test_reprompt_leaves_no_temp_dir(harness, memory_store):
    await harness.complete_job(memory_store)

    await harness.reprompt(memory_store)

    assert list((harness.clips / "_tmp").iterdir()) == []


# ── failure ───────────────────────────────────────────────────────────────────


async def test_failed_reprompt_keeps_the_old_clips(harness, store):
    old = await harness.complete_job(store)
    before = await store.get(JOB_ID)
    harness.fail_on = {"03_Gamma.mp4"}

    recorder = await harness.reprompt(
        store, target_length_min_seconds=25, target_length_max_seconds=45
    )

    assert harness.renders == [str(harness.clips / "02_Alpha.mp4")]
    assert [c["clip_id"] for c in await _live(store)] == [c["clip_id"] for c in old]
    job = await store.get(JOB_ID)
    assert job.status == "completed"
    assert job.error is None
    assert job.prompt == before.prompt
    assert job.pipeline_options == before.pipeline_options
    assert sorted(job.output_paths) == sorted(before.output_paths)
    types = recorder.types()
    assert EventType.JOB_FAILED not in types
    assert EventType.JOB_COMPLETED not in types
    assert EventType.JOB_REPROMPTED not in types
    assert types[-1] is EventType.REPROMPT_FAILED
    assert "render exploded on 03_Gamma.mp4" in recorder.events[-1].payload["error"]
    # The clip that did render is not left behind, live or on disk.
    assert not (harness.clips / "02_Alpha.mp4").exists()
    assert not (harness.clips / "02_Alpha_thumb.jpg").exists()
    for clip in old:
        assert Path(clip["output_path"]).is_file()
    assert list((harness.clips / "_tmp").iterdir()) == []


async def test_failed_reprompt_drops_its_chapters_from_the_job(harness, memory_store):
    await harness.complete_job(memory_store)
    harness.fail_on = {"03_Gamma.mp4"}

    await harness.reprompt(memory_store)

    assert sorted((await memory_store.get(JOB_ID)).chapters) == [0, 1]


async def test_reprompt_with_no_kept_segment_fails_softly(harness, memory_store):
    old = await harness.complete_job(memory_store)
    harness.proposer.results = [[]]

    recorder = await harness.reprompt(memory_store)

    assert [c["clip_id"] for c in await _live(memory_store)] == [
        c["clip_id"] for c in old
    ]
    assert recorder.types()[-1] is EventType.REPROMPT_FAILED
    assert (await memory_store.get(JOB_ID)).status == "completed"


async def test_reprompt_when_the_source_is_gone_fails_softly(harness, memory_store):
    old = await harness.complete_job(memory_store)
    harness.source.unlink()

    recorder = await harness.reprompt(memory_store)

    assert len(await _live(memory_store)) == len(old)
    (failed,) = recorder.of(EventType.REPROMPT_FAILED)
    assert "source video not retained" in failed.payload["error"]
    assert EventType.JOB_FAILED not in recorder.types()


async def test_reprompt_range_past_the_source_end_fails_softly(harness, memory_store):
    old = await harness.complete_job(memory_store)

    recorder = await harness.reprompt(
        memory_store, start_seconds=SOURCE_SECONDS + 5, end_seconds=SOURCE_SECONDS + 30
    )

    assert len(await _live(memory_store)) == len(old)
    assert recorder.types()[-1] is EventType.REPROMPT_FAILED


async def test_reprompt_range_overlapping_the_end_is_clamped(harness, memory_store):
    await harness.complete_job(memory_store)

    await harness.reprompt(memory_store, start_seconds=340.0, end_seconds=400.0)

    (clip,) = await _live(memory_store)
    assert (clip["start"], clip["end"]) == (340.0, SOURCE_SECONDS)


@pytest.mark.parametrize("fail", [False, True])
async def test_reprompt_releases_its_in_flight_claim(harness, memory_store, fail):
    await harness.complete_job(memory_store)
    assert orch.claim_reprompt(JOB_ID) is True
    assert orch.claim_reprompt(JOB_ID) is False
    if fail:
        harness.fail_on = {"03_Gamma.mp4"}

    await harness.reprompt(memory_store)

    assert orch.reprompt_in_flight(JOB_ID) is False
    assert orch.claim_reprompt(JOB_ID) is True


# ── restart safety ────────────────────────────────────────────────────────────


def _rerender_trigger(clip: dict[str, Any]) -> Event:
    return Event(
        type=EventType.VIDEO_REQUESTED,
        job_id=JOB_ID,
        payload={"rerender_clip_id": clip["clip_id"], "url": URL},
    )


async def test_reprompt_interrupted_restart_restores_completed(harness, store):
    """A restart mid-reprompt (``fail_interrupted_jobs`` at startup) must not
    fail the job: it stays ``completed`` with its old clips live and
    re-renderable, and no half-made new clip shows up."""
    old = await harness.complete_job(store)
    started, release = threading.Event(), threading.Event()
    harness.block = ("02_Alpha.mp4", started, release)

    task = asyncio.create_task(harness.reprompt(store))
    assert await asyncio.to_thread(started.wait, 5)

    # Mid-reprompt: the job is not "running", so a restart leaves it alone.
    assert await store.fail_interrupted_jobs() == []
    assert (await store.get(JOB_ID)).status == "completed"
    assert [c["clip_id"] for c in await _live(store)] == [c["clip_id"] for c in old]

    # The process dies (shutdown cancels the pipeline), then starts again.
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await store.fail_interrupted_jobs() == []

    job = await store.get(JOB_ID)
    assert job.status == "completed"
    assert job.error is None
    assert [c["clip_id"] for c in await _live(store)] == [c["clip_id"] for c in old]
    assert not orch.reprompt_in_flight(JOB_ID)
    harness.renders.clear()
    await orch._run_job(_rerender_trigger(old[0]), AsyncEventBus(), store)
    assert harness.renders == [old[0]["output_path"]]


# ── SSE ───────────────────────────────────────────────────────────────────────


def _request(bus: AsyncEventBus, store: Any) -> SimpleNamespace:
    state = SimpleNamespace(event_bus=bus, job_store=store)
    return SimpleNamespace(app=SimpleNamespace(state=state))


async def test_sse_after_reprompt_not_closed_by_replayed_completion(
    harness, memory_store
):
    """The event bus replays a job's history to every new SSE subscriber and
    the stream closes on the first JobCompleted, so the old run's completion
    must not be replayed once a reprompt has started."""
    bus = harness.bus
    await harness.complete_job(memory_store)
    (old_completed,) = harness.recorder.of(EventType.JOB_COMPLETED)

    recorder = await harness.reprompt(memory_store)
    (new_completed,) = recorder.of(EventType.JOB_COMPLETED)

    response = await jobs_router.stream_job_events(JOB_ID, _request(bus, memory_store))
    items = [item async for item in response.body_iterator]

    ids = [item["id"] for item in items]
    assert old_completed.event_id not in ids
    assert ids[-1] == new_completed.event_id
    names = [item["event"] for item in items]
    assert "ClipRendered" in names
    assert names[-2:] == ["JobReprompted", "JobCompleted"]


async def test_sse_stream_ends_on_reprompt_failed(harness, memory_store):
    bus = harness.bus
    await harness.complete_job(memory_store)
    harness.fail_on = {"03_Gamma.mp4"}

    await harness.reprompt(memory_store)

    response = await jobs_router.stream_job_events(JOB_ID, _request(bus, memory_store))
    items = await asyncio.wait_for(_collect(response.body_iterator), timeout=2.0)
    assert items[-1]["event"] == "RepromptFailed"


async def _collect(iterator: Any) -> list[dict[str, Any]]:
    return [item async for item in iterator]
