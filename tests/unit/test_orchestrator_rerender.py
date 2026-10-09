"""Single-clip re-render in the orchestrator (FR-015).

A VIDEO_REQUESTED trigger carrying ``rerender_clip_id`` re-renders that clip
in place from the job's saved source video. The job stays ``completed``: no
JobCompleted/JobFailed, and a failed re-render leaves the clip untouched.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
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
from app.services.platforms import resolve as real_resolve_adapter
from app.services.transcription_service import WordTiming
from app.workers import orchestrator as orch

JOB_ID = "job-1"
CLIP_ID = "c1-0000-aaaa"
URL = "https://www.youtube.com/watch?v=rerender001"


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


class _Recorder:
    """Records published events, then forwards them to the real bus."""

    def __init__(self, bus: AsyncEventBus) -> None:
        self.events: list[Event] = []
        self._publish = bus.publish

    async def __call__(self, event: Event) -> None:
        self.events.append(event)
        await self._publish(event)

    def types(self) -> list[EventType]:
        return [e.type for e in self.events]


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    base = tmp_path / "vid"
    clips = base / "clips"
    clips.mkdir(parents=True)
    source = base / "video.mp4"
    source.write_bytes(b"source")
    output = clips / "01_Outro.mp4"
    output.write_bytes(b"old")
    thumb = clips / "01_Outro_thumb.jpg"
    thumb.write_bytes(b"old-thumb")
    return {"clips": clips, "source": source, "output": output, "thumb": thumb}


async def _seed(store: Any, layout: dict[str, Path]) -> None:
    await store.create(JobState(job_id=JOB_ID, url=URL, download_path="/downloads"))

    def _complete(s: JobState) -> None:
        s.status = "completed"
        s.current_step = "completed"
        s.output_paths = [str(layout["output"])]
        s.video_path = str(layout["source"])

    await store.update(JOB_ID, _complete)
    await store.upsert_clip(
        JOB_ID,
        CLIP_ID,
        lambda c: c.update(
            {
                "start": 6.0,
                "end": 12.0,
                "output_path": str(layout["output"]),
                "thumbnail_path": str(layout["thumb"]),
                "title": "Outro",
                "transcript": "old words",
                "liked": True,
            }
        ),
    )


def _trigger(**overrides: Any) -> Event:
    payload: dict[str, Any] = {
        "rerender_clip_id": CLIP_ID,
        "url": URL,
        "download_path": "/downloads",
        "caption_format": "srt",
        "target_aspect_ratio": 9 / 16,
        "language": "en-US",
        "pipeline_options": PipelineOptions(audio_enhance=False).model_dump(),
        "reframe_provider": "letterbox",
    }
    payload.update(overrides)
    return Event(type=EventType.VIDEO_REQUESTED, job_id=JOB_ID, payload=payload)


@pytest.fixture
def render_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    def _render(video_path, output_path, start, end, *args, **kwargs):
        calls.append((video_path, output_path, start, end))
        Path(output_path).write_bytes(b"new")
        return output_path

    def _thumb(clip_path: str, output_path: str) -> str:
        Path(output_path).write_bytes(b"new-thumb")
        return output_path

    def _extract_audio(src, start, duration, wav_path):
        Path(wav_path).write_bytes(b"\x00")
        return wav_path

    monkeypatch.setattr(orch.render_service, "render_clip", _render)
    monkeypatch.setattr(orch.thumbnail_service, "generate_thumbnail", _thumb)
    monkeypatch.setattr(orch.clip_service, "extract_audio", _extract_audio)
    monkeypatch.setattr(
        orch.transcription_service,
        "transcribe_to_words",
        lambda audio_path, **_: [
            WordTiming("new", 0.0, 0.5),
            WordTiming("words", 0.5, 1.0),
        ],
    )
    # Must never be reached: a re-render does not download or resolve the URL.
    monkeypatch.setattr(
        orch,
        "resolve_adapter",
        lambda url: pytest.fail("re-render resolved an adapter"),
    )
    return calls


def _job_view(job: JobState) -> tuple[Any, ...]:
    return (job.status, job.current_step, job.error, sorted(job.output_paths))


async def test_rerender_updates_same_clip_in_place(store, layout, render_calls):
    await _seed(store, layout)
    before = await store.get(JOB_ID)
    bus = AsyncEventBus()
    recorder = _Recorder(bus)
    bus.publish = recorder  # type: ignore[method-assign]

    await orch._run_job(_trigger(), bus, store)

    assert render_calls == [(str(layout["source"]), str(layout["output"]), 6.0, 12.0)]
    after = await store.get(JOB_ID)
    assert after.status == "completed"
    assert _job_view(after) == _job_view(before)
    clips = await store.list_clips(job_id=JOB_ID)
    assert [c["clip_id"] for c in clips] == [CLIP_ID]
    clip = clips[0]
    assert clip["output_path"] == str(layout["output"])
    assert clip["thumbnail_path"] == str(layout["thumb"])
    assert clip["transcript"] == "new words"
    assert clip["liked"] is True
    assert layout["output"].read_bytes() == b"new"
    assert layout["thumb"].read_bytes() == b"new-thumb"
    types = recorder.types()
    assert EventType.JOB_COMPLETED not in types
    assert EventType.JOB_FAILED not in types
    rendered = [e for e in recorder.events if e.type is EventType.CLIP_RENDERED]
    assert len(rendered) == 1
    assert rendered[0].payload["chapter_index"] == 1
    assert EventType.THUMBNAIL_GENERATED in types
    assert not (layout["clips"] / "_tmp" / f"{JOB_ID}-rerender-{CLIP_ID[:8]}").exists()


async def test_failed_rerender_leaves_job_and_clip_untouched(
    store, layout, render_calls, monkeypatch
):
    await _seed(store, layout)
    before_job = await store.get(JOB_ID)
    before_clip = await store.get_clip(CLIP_ID)

    def _boom(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(orch.render_service, "render_clip", _boom)
    bus = AsyncEventBus()
    recorder = _Recorder(bus)
    bus.publish = recorder  # type: ignore[method-assign]

    await orch._run_job(_trigger(), bus, store)  # must not raise

    after_job = await store.get(JOB_ID)
    assert after_job.status == "completed"
    assert _job_view(after_job) == _job_view(before_job)
    assert await store.get_clip(CLIP_ID) == before_clip
    assert layout["output"].read_bytes() == b"old"
    assert EventType.JOB_FAILED not in recorder.types()
    assert EventType.JOB_COMPLETED not in recorder.types()
    assert not (layout["clips"] / "_tmp" / f"{JOB_ID}-rerender-{CLIP_ID[:8]}").exists()


async def test_old_empty_url_rerender_payload_no_longer_fails_the_job(
    store, layout, render_calls, monkeypatch
):
    """Before FR-015 the router queued ``url: ""``; the orchestrator then ran
    the full pipeline and failed the completed job (No adapter matches URL)."""
    monkeypatch.setattr(orch, "resolve_adapter", real_resolve_adapter)
    await _seed(store, layout)
    bus = AsyncEventBus()
    recorder = _Recorder(bus)
    bus.publish = recorder  # type: ignore[method-assign]
    old_payload = {
        "url": "",
        "download_path": "/tmp/yt",
        "caption_format": "srt",
        "target_aspect_ratio": 9 / 16,
        "rerender_clip_id": CLIP_ID,
        "reframe_provider": "letterbox",
    }

    await orch._run_job(
        Event(type=EventType.VIDEO_REQUESTED, job_id=JOB_ID, payload=old_payload),
        bus,
        store,
    )

    job = await store.get(JOB_ID)
    assert job.status == "completed"
    assert job.error is None
    assert EventType.JOB_FAILED not in recorder.types()


async def test_rerender_of_unknown_clip_is_a_logged_no_op(store, layout, render_calls):
    await _seed(store, layout)
    before = await store.get(JOB_ID)
    bus = AsyncEventBus()
    recorder = _Recorder(bus)
    bus.publish = recorder  # type: ignore[method-assign]

    await orch._run_job(_trigger(rerender_clip_id="nope"), bus, store)

    assert render_calls == []
    assert _job_view(await store.get(JOB_ID)) == _job_view(before)
    assert recorder.events == []


async def test_rerender_cancellation_propagates(
    store, layout, render_calls, monkeypatch
):
    await _seed(store, layout)
    started = asyncio.Event()

    async def _hang(**kwargs: Any) -> str | None:
        started.set()
        await asyncio.Event().wait()
        return None

    monkeypatch.setattr(orch, "_process_chapter", _hang)
    task = asyncio.create_task(orch._run_job(_trigger(), AsyncEventBus(), store))
    await asyncio.wait_for(started.wait(), timeout=5)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert (await store.get(JOB_ID)).status == "completed"
    assert not (layout["clips"] / "_tmp" / f"{JOB_ID}-rerender-{CLIP_ID[:8]}").exists()
