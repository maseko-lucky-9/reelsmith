"""The orchestrator reports the optional audio, filler and AI-hook stages (T016).

``AUDIO_ENHANCED``, ``FILLERS_REMOVED`` and ``AI_HOOK_GENERATED`` are published
by the orchestrator itself: the services run in worker threads, where their
``emit_from_sync`` helper has no event loop and does nothing. Each event fires
once per chapter, right after its stage succeeds, and never when the stage is
off, skipped or failed (those stages stay non-fatal).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import JobStore
from app.domain.events import Event, EventType
from app.domain.models import JobState
from app.services.transcription_service import WordTiming
from app.workers import orchestrator as orch
from tests.unit.test_orchestrator import (
    _fake_extract_audio,
    _fake_render,
    _fake_subfolder,
    _FakeAdapter,
)

JOB_ID = "job-stage-events"
STAGE_EVENTS = (
    EventType.AUDIO_ENHANCED,
    EventType.FILLERS_REMOVED,
    EventType.AI_HOOK_GENERATED,
)
# Per-chapter pipeline order, as sequenced by _process_chapter.
CHAPTER_ORDER = (
    EventType.CHAPTER_CLIP_EXTRACTED,
    EventType.AUDIO_ENHANCED,
    EventType.CHAPTER_TRANSCRIBED,
    EventType.FILLERS_REMOVED,
    EventType.CAPTIONS_GENERATED,
    EventType.CLIP_RENDERED,
    EventType.AI_HOOK_GENERATED,
    EventType.SOCIAL_CONTENT_GENERATED,
)
WORDS = [
    WordTiming("um", 0.0, 0.2),
    WordTiming("hello", 0.2, 0.6),
    WordTiming("world", 0.6, 1.0),
]
ALL_ON = {"audio_enhance": True, "filler_removal": True, "ai_hook": True}
ALL_OFF = {"audio_enhance": False, "filler_removal": False, "ai_hook": False}


def _fake_enhance(in_path: str, out_path: str, **_: Any) -> str:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(b"\x00")
    return out_path


def _failing(*_: Any, **__: Any) -> Any:
    raise RuntimeError("stage blew up")


async def _run_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pipeline_options: dict[str, bool],
    *,
    enhance: Callable[..., Any] = _fake_enhance,
    generate_hook: Callable[..., Any] = lambda text, **_: "Stop scrolling",
    words: list[WordTiming] = WORDS,
    extract_audio: Callable[..., Any] = _fake_extract_audio,
) -> tuple[list[Event], JobStore]:
    """Run one two-chapter job end to end with stubbed services."""
    bus = AsyncEventBus()
    store = JobStore()
    state = JobState(
        job_id=JOB_ID,
        url="https://www.youtube.com/watch?v=fake",
        source="youtube",
        download_path=str(tmp_path),
    )
    await store.create(state)

    monkeypatch.setattr(orch.folder_service, "create_video_subfolder", _fake_subfolder)
    monkeypatch.setattr(orch, "resolve_adapter", lambda url: _FakeAdapter())
    monkeypatch.setattr(orch.clip_service, "probe_safe_end", lambda path: 999.0)
    monkeypatch.setattr(orch.clip_service, "extract_audio", extract_audio)
    monkeypatch.setattr(orch.audio_enhance_service, "enhance", enhance)
    monkeypatch.setattr(
        orch.transcription_service,
        "transcribe_to_words",
        lambda audio_path, **_: list(words),
    )
    monkeypatch.setattr(orch.render_service, "render_clip", _fake_render)
    monkeypatch.setattr(orch.ai_hook_service, "generate_hook", generate_hook)
    monkeypatch.setattr(orch.settings, "ollama_enabled", False)
    monkeypatch.setattr(orch.settings, "max_parallel_chapters", 1)

    received: list[Event] = []

    async def collect() -> None:
        async for event in bus.subscribe(job_id=JOB_ID):
            received.append(event)
            if event.type in (EventType.JOB_COMPLETED, EventType.JOB_FAILED):
                return

    collector = asyncio.create_task(collect())
    orchestrator = asyncio.create_task(orch.run_orchestrator(bus, store))
    for _ in range(5):
        await asyncio.sleep(0.01)

    await bus.publish(
        Event(
            type=EventType.VIDEO_REQUESTED,
            job_id=JOB_ID,
            payload={
                "url": state.url,
                "download_path": state.download_path,
                "caption_format": "srt",
                "target_aspect_ratio": 9 / 16,
                "pipeline_options": pipeline_options,
            },
        )
    )
    await asyncio.wait_for(collector, timeout=10)
    orchestrator.cancel()
    try:
        await orchestrator
    except asyncio.CancelledError:
        pass
    return received, store


def _chapter_types(events: list[Event], index: int) -> list[EventType]:
    return [e.type for e in events if e.payload.get("chapter_index") == index]


def _assert_job_completed(events: list[Event]) -> None:
    assert events[-1].type is EventType.JOB_COMPLETED, [e.type for e in events]


@pytest.mark.asyncio
async def test_stage_events_fire_once_per_chapter_in_pipeline_order(
    tmp_path, monkeypatch
):
    events, store = await _run_job(tmp_path, monkeypatch, ALL_ON)

    _assert_job_completed(events)
    types = [e.type for e in events]
    for stage_event in STAGE_EVENTS:
        assert types.count(stage_event) == 2, stage_event
        assert types.index(stage_event) < types.index(EventType.JOB_COMPLETED)

    for index in (0, 1):
        chapter = _chapter_types(events, index)
        for stage_event in STAGE_EVENTS:
            assert chapter.count(stage_event) == 1, (index, stage_event, chapter)
        observed = [t for t in chapter if t in CHAPTER_ORDER]
        assert observed == list(CHAPTER_ORDER), (index, observed)


@pytest.mark.asyncio
async def test_stage_event_payloads_identify_the_chapter_and_clip(
    tmp_path, monkeypatch
):
    events, store = await _run_job(tmp_path, monkeypatch, ALL_ON)

    _assert_job_completed(events)
    clips = await store.list_clips(JOB_ID)
    clip_ids = {c["clip_id"] for c in clips}
    for index in (0, 1):
        by_type = {
            e.type: e.payload for e in events if e.payload.get("chapter_index") == index
        }
        enhanced = by_type[EventType.AUDIO_ENHANCED]
        assert enhanced["provider"] == orch.settings.audio_enhance_provider
        assert enhanced["audio_path"].endswith(f"chapter_{index}_enhanced.wav")

        fillers = by_type[EventType.FILLERS_REMOVED]
        assert fillers["words_removed"] == 1
        assert fillers["words_kept"] == 2

        hook = by_type[EventType.AI_HOOK_GENERATED]
        assert hook["hook"] == "Stop scrolling"
        assert hook["clip_id"] in clip_ids


@pytest.mark.asyncio
async def test_no_stage_events_when_options_are_off(tmp_path, monkeypatch):
    events, _ = await _run_job(tmp_path, monkeypatch, ALL_OFF)

    _assert_job_completed(events)
    types = {e.type for e in events}
    assert types.isdisjoint(STAGE_EVENTS)
    skipped = {
        e.payload["stage_id"] for e in events if e.type is EventType.STAGE_SKIPPED
    }
    assert {"audio_enhance", "filler_removal", "ai_hook"} <= skipped


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "generate_hook",
    [_failing, lambda text, **_: ""],
    ids=["hook-raises", "hook-empty"],
)
async def test_failed_stages_emit_nothing_and_stay_non_fatal(
    tmp_path, monkeypatch, generate_hook
):
    events, store = await _run_job(
        tmp_path, monkeypatch, ALL_ON, enhance=_failing, generate_hook=generate_hook
    )

    _assert_job_completed(events)
    types = [e.type for e in events]
    assert EventType.AUDIO_ENHANCED not in types
    assert EventType.AI_HOOK_GENERATED not in types
    # Filler removal still ran on the (original-audio) transcript.
    assert types.count(EventType.FILLERS_REMOVED) == 2
    assert types.count(EventType.CLIP_RENDERED) == 2
    assert (await store.get(JOB_ID)).status == "completed"


@pytest.mark.asyncio
async def test_no_stage_events_when_enabled_stages_have_no_input(tmp_path, monkeypatch):
    """Options on, but the source has no audio: nothing to enhance, clean or hook."""
    events, _ = await _run_job(
        tmp_path,
        monkeypatch,
        ALL_ON,
        enhance=_failing,  # must not even be called
        extract_audio=lambda *a, **k: None,
        words=[],
    )

    _assert_job_completed(events)
    types = {e.type for e in events}
    assert types.isdisjoint(STAGE_EVENTS), types
    assert [e.type for e in events].count(EventType.CLIP_RENDERED) == 2
