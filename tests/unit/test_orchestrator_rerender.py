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


@pytest.mark.parametrize(
    ("prior_status", "expected"),
    [("completed", "completed"), ("failed", "failed"), (None, "completed")],
)
async def test_failed_rerender_resets_chapter_status(
    layout, render_calls, monkeypatch, prior_status, expected
):
    """T028(a): a render that raises must not leave the chapter ``rendering``.

    Only the memory store keeps chapter status (the SQL store persists no
    chapter status), so this runs on the memory store.
    """
    store = InMemoryJobStore()
    await _seed(store, layout)
    if prior_status is not None:
        await store.upsert_chapter(
            JOB_ID, lambda c: setattr(c, "status", prior_status), 1
        )
    before_clip = dict(await store.get_clip(CLIP_ID))

    def _boom(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(orch.render_service, "render_clip", _boom)

    await orch._run_job(_trigger(), AsyncEventBus(), store)

    job = await store.get(JOB_ID)
    assert job.chapters[1].status == expected
    assert await store.get_clip(CLIP_ID) == before_clip
    assert job.status == "completed"


@pytest.fixture
def copy_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Stub the social-copy generators (LLM) and count their calls."""
    calls = {"social": 0, "hook": 0}

    def _social(title, transcript, *args, **kwargs):
        calls["social"] += 1
        return "new summary", ["#new"]

    def _hook(transcript, **kwargs):
        calls["hook"] += 1
        return "new hook"

    monkeypatch.setattr(orch.settings, "ollama_enabled", True)
    monkeypatch.setattr(orch.ollama_service, "generate_social_content", _social)
    monkeypatch.setattr(orch.ai_hook_service, "generate_hook", _hook)
    return calls


async def _seed_copy(store: Any, layout: dict[str, Path]) -> None:
    await _seed(store, layout)
    await store.upsert_clip(
        JOB_ID,
        CLIP_ID,
        lambda c: c.update(
            {
                "summary": "old summary",
                "hashtags": ["#old"],
                "ai_hook_text": "old hook",
            }
        ),
    )


def _copy_view(clip: dict[str, Any]) -> tuple[Any, ...]:
    return (clip["title"], clip["summary"], clip["hashtags"], clip["ai_hook_text"])


_HOOK_ON = PipelineOptions(audio_enhance=False, ai_hook=True).model_dump()


async def test_rerender_without_copy_keeps_title_summary_hashtags_and_hook(
    store, layout, render_calls, copy_calls
):
    """T028 (d): ``regenerate_copy=False`` re-renders the video only."""
    await _seed_copy(store, layout)

    await orch._run_job(
        _trigger(pipeline_options=_HOOK_ON, regenerate_copy=False),
        AsyncEventBus(),
        store,
    )

    assert len(render_calls) == 1
    clip = await store.get_clip(CLIP_ID)
    assert _copy_view(clip) == ("Outro", "old summary", ["#old"], "old hook")
    assert clip["transcript"] == "new words"
    assert copy_calls == {"social": 0, "hook": 0}


@pytest.mark.parametrize("flag", [{}, {"regenerate_copy": True}])
async def test_rerender_regenerates_copy_by_default(
    store, layout, render_calls, copy_calls, flag
):
    await _seed_copy(store, layout)

    await orch._run_job(
        _trigger(pipeline_options=_HOOK_ON, **flag), AsyncEventBus(), store
    )

    clip = await store.get_clip(CLIP_ID)
    assert _copy_view(clip) == ("Outro", "new summary", ["#new"], "new hook")
    assert copy_calls == {"social": 1, "hook": 1}


# ── Re-render of an edited clip (spec 016, plan task 4) ───────────────────────


async def _save_edit(store: Any, timeline: dict[str, Any]) -> None:
    """Save ``timeline`` as the clip's ``ClipEdit``, as the editor's PUT does."""
    if isinstance(store, InMemoryJobStore):
        await store.put_clip_edit(CLIP_ID, timeline)
        return
    async with store._factory() as session:
        session.add(_models.ClipEdit(clip_id=CLIP_ID, timeline=timeline, version=1))
        await session.commit()


@pytest.fixture
def plan_renders(
    monkeypatch: pytest.MonkeyPatch, render_calls: list[tuple[Any, ...]]
) -> list[dict[str, Any]]:
    """Every render_clip call with its keyword arguments; the source is 60 s
    long, and nothing may transcribe (an edit brings its own words)."""
    calls: list[dict[str, Any]] = []

    def _render(video_path, output_path, start, end, *args, **kwargs):
        calls.append({"video_path": video_path, "start": start, "end": end, **kwargs})
        Path(output_path).write_bytes(b"edited")
        return output_path

    monkeypatch.setattr(orch.render_service, "render_clip", _render)
    monkeypatch.setattr(orch.ffmpeg_tools, "duration", lambda path, *a, **k: 60.0)
    monkeypatch.setattr(
        orch.transcription_service,
        "transcribe_to_words",
        lambda *a, **k: pytest.fail("an edited re-render transcribed"),
    )
    return calls


def _v2_edit() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "caption_style": "hormozi",
        "mute_source": True,
        "tracks": [
            # Trims the head and extends past the clip's end (12 s), with a cut.
            {"kind": "video", "items": [{"start": 7.0, "end": 9.0}, {"start": 10.0, "end": 13.0}]},
            {
                "kind": "caption",
                "items": [
                    {
                        "start": 0.0,
                        "end": 2.0,
                        "text": "hello there",
                        "words": [
                            {"word": "helo", "start": 0.2, "end": 0.8},
                            {"word": "there", "start": 0.9, "end": 1.5},
                        ],
                    }
                ],
            },
            {"kind": "text-overlay", "items": [{"start": 0.0, "end": 1.0, "text": "Watch", "x": 0.5, "y": 0.1}]},
            {"kind": "audio", "items": [{"start": 0.5, "end": 2.5, "src": "vo.wav", "gain": 1.5}]},
        ],
    }


async def test_rerender_renders_the_saved_v2_edit(store, layout, plan_renders):
    await _seed(store, layout)
    (layout["clips"] / "vo.wav").write_bytes(b"RIFF")
    await _save_edit(store, _v2_edit())

    await orch._run_job(_trigger(), AsyncEventBus(), store)

    assert len(plan_renders) == 1
    call = plan_renders[0]
    assert call["video_path"] == str(layout["source"])
    assert (call["start"], call["end"]) == (7.0, 13.0)
    plan = call["plan"]
    assert [(s.start, s.end) for s in plan.segments] == [(7.0, 9.0), (10.0, 13.0)]
    assert plan.source_path == str(layout["source"])
    assert plan.caption_style == "hormozi"
    assert plan.mute_source is True
    assert [c.text for c in plan.captions] == ["hello there"]
    assert [o.text for o in plan.overlays] == ["Watch"]
    assert [(a.path, a.gain) for a in plan.audio] == [(str((layout["clips"] / "vo.wav").resolve()), 1.5)]
    assert call.get("broll") is None
    assert layout["output"].read_bytes() == b"edited"
    clip = await store.get_clip(CLIP_ID)
    assert clip["transcript"] == "hello there"
    assert (clip["start"], clip["end"]) == (6.0, 12.0)
    assert clip["thumbnail_path"] == str(layout["thumb"])
    assert layout["thumb"].read_bytes() == b"new-thumb"
    # The words sidecar holds what the edited reel says, on its clock.
    words = orch.clip_words.read_clip_words(str(layout["output"]))
    assert [(w.word, w.start, w.end) for w in words] == [("hello", 0.2, 0.8), ("there", 0.9, 1.5)]


async def test_rerender_of_a_v1_edit_reads_the_reel_window_of_the_source(store, layout, plan_renders):
    """A v1 ``main`` item is the reel: its ``trim_start`` counts from the
    clip's start (6 s), never from the source's opening."""
    await _seed(store, layout)
    await _save_edit(
        store,
        {"tracks": [{"kind": "video", "items": [{"start": 0.0, "end": 2.0, "src": "main", "trim_start": 1.0}]}]},
    )

    await orch._run_job(_trigger(), AsyncEventBus(), store)

    assert [(c["start"], c["end"]) for c in plan_renders] == [(7.0, 9.0)]
    item = plan_renders[0]["plan"].video[0]
    assert (item.src, item.trim_start) == (str(layout["source"]), 7.0)


async def test_rerender_without_an_edit_passes_no_plan(store, layout, plan_renders, monkeypatch):
    await _seed(store, layout)
    monkeypatch.setattr(
        orch.transcription_service, "transcribe_to_words", lambda *a, **k: [WordTiming("new", 0.0, 0.5)]
    )

    await orch._run_job(_trigger(), AsyncEventBus(), store)

    assert [(c["start"], c["end"]) for c in plan_renders] == [(6.0, 12.0)]
    assert "plan" not in plan_renders[0]


@pytest.mark.parametrize(
    "timeline",
    [
        # A segment past the source's end (60 s).
        {"schema_version": 2, "tracks": [{"kind": "video", "items": [{"start": 70.0, "end": 80.0}]}]},
        # A v1 item reading a file that is not the job's source.
        {"tracks": [{"kind": "video", "items": [{"start": 0, "end": 2, "src": "/etc/other.mp4"}]}]},
        # A voice-over that is not in the clip's folder.
        {
            "schema_version": 2,
            "tracks": [
                {"kind": "video", "items": [{"start": 6.0, "end": 8.0}]},
                {"kind": "audio", "items": [{"start": 0, "end": 1, "src": "missing.wav"}]},
            ],
        },
    ],
    ids=["outside-source", "foreign-v1-src", "missing-voiceover"],
)
async def test_an_unrenderable_edit_fails_the_rerender_cleanly(
    store, layout, plan_renders, timeline, caplog
):
    await _seed(store, layout)
    if isinstance(store, InMemoryJobStore):
        await store.upsert_chapter(JOB_ID, lambda c: setattr(c, "status", "completed"), 1)
    before_job = await store.get(JOB_ID)
    before_clip = dict(await store.get_clip(CLIP_ID))
    await _save_edit(store, timeline)
    bus = AsyncEventBus()
    recorder = _Recorder(bus)
    bus.publish = recorder  # type: ignore[method-assign]

    with caplog.at_level("WARNING", logger=orch.log.name):
        await orch._run_job(_trigger(), bus, store)  # must not raise

    assert plan_renders == []
    assert layout["output"].read_bytes() == b"old"
    assert await store.get_clip(CLIP_ID) == before_clip
    after_job = await store.get(JOB_ID)
    assert _job_view(after_job) == _job_view(before_job)
    if isinstance(store, InMemoryJobStore):
        assert after_job.chapters[1].status == "completed"
    refused = [r for r in caplog.records if "saved edit cannot be rendered" in r.getMessage()]
    assert [r.levelname for r in refused] == ["WARNING"]
    assert EventType.JOB_FAILED not in recorder.types()
    assert EventType.CLIP_RENDERED not in recorder.types()
    assert not (layout["clips"] / "_tmp" / f"{JOB_ID}-rerender-{CLIP_ID[:8]}").exists()


async def test_a_user_cancel_of_an_edited_rerender_keeps_the_old_clip(layout, plan_renders, monkeypatch):
    """Spec 013 with an edit: the render is stopped, the clip file and record
    stay as they were and the chapter gets its status back."""
    import threading

    store = InMemoryJobStore()
    await _seed(store, layout)
    await store.upsert_chapter(JOB_ID, lambda c: setattr(c, "status", "completed"), 1)
    (layout["clips"] / "vo.wav").write_bytes(b"RIFF")
    await _save_edit(store, _v2_edit())
    before_clip = dict(await store.get_clip(CLIP_ID))
    entered = threading.Event()
    plans: list[Any] = []

    def _render_until_killed(video_path, output_path, start, end, *args, **kwargs):
        plans.append(kwargs.get("plan"))
        entered.set()
        cancel = orch.ffmpeg_tools.current_cancel_event()
        while not cancel.is_set():
            cancel.wait(0.01)
        raise RuntimeError("ffmpeg killed")

    monkeypatch.setattr(orch.render_service, "render_clip", _render_until_killed)
    orch._user_cancelled.discard(JOB_ID)
    task = asyncio.create_task(orch._run_job(_trigger(), AsyncEventBus(), store))
    orch._job_tasks.setdefault(JOB_ID, set()).add(task)
    try:
        await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), timeout=6)
        assert orch.cancel_job(JOB_ID) is True
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        orch._job_tasks.pop(JOB_ID, None)
        orch._user_cancelled.discard(JOB_ID)
        orch._user_cancelled_tasks.discard(task)

    assert plans and plans[0] is not None and plans[0].caption_style == "hormozi"
    assert layout["output"].read_bytes() == b"old"
    assert await store.get_clip(CLIP_ID) == before_clip
    job = await store.get(JOB_ID)
    assert job.status == "completed"
    assert job.chapters[1].status == "completed"
    assert not (layout["clips"] / "_tmp" / f"{JOB_ID}-rerender-{CLIP_ID[:8]}").exists()
