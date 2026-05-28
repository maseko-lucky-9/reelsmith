"""Integration coverage for W1-W3 stage gates wired into the orchestrator.

These tests use the in-memory ``JobStore`` and mock every service, so they
do NOT need the ``integration`` marker (no real network, no real ffmpeg).
They live under ``tests/integration/`` because they cover the orchestrator
*as a whole* rather than a single unit — every gate (audio_enhance,
ai_hook, filler_removal) is exercised end-to-end through
``run_orchestrator``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import JobStore
from app.domain.events import Event, EventType
from app.domain.models import JobState, PipelineOptions
from app.services.platforms.base import Chapter, DownloadResult
from app.services.transcription_service import WordTiming
from app.workers import orchestrator as orch


def _fake_subfolder(download_path: str, url: str, platform_id: str = "video"):
    base = Path(download_path) / "vid"
    clips = base / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    return str(base), str(clips)


class _FakeAdapter:
    platform_id = "youtube"

    def download(self, url: str, destination_folder: str) -> DownloadResult:
        video_path = str(Path(destination_folder) / "video.mp4")
        Path(video_path).write_bytes(b"\x00")
        info = {
            "title": "Test",
            "duration": 10.0,
            "chapters": [
                {"title": "Ch1", "start_time": 0.0, "end_time": 10.0},
            ],
        }
        return DownloadResult(
            video_path=video_path,
            info=info,
            title="Test",
            duration=10.0,
            source=self.platform_id,
        )

    def extract_chapters(self, info: dict) -> list[Chapter]:
        raw = info.get("chapters") or []
        return [
            Chapter(
                index=i, title=c["title"],
                start=float(c["start_time"]), end=float(c["end_time"]),
            )
            for i, c in enumerate(raw)
        ]


def _fake_render(video_path, output_path, *args, **kwargs):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(b"\x00")
    return output_path


def _fake_render_to_path(text, videosize, path, font_size=50):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"\x00")
    return path


# ── shared scaffolding ──────────────────────────────────────────────────────


def _install_pipeline_doubles(
    monkeypatch,
    *,
    words: list[WordTiming],
    enhance_calls: list[tuple[Any, ...]],
    hook_calls: list[tuple[Any, ...]],
    hook_return: str = "",
) -> None:
    """Patch every external surface the orchestrator touches.

    ``enhance_calls`` and ``hook_calls`` are recorders the tests assert on.
    """
    monkeypatch.setattr(orch.folder_service, "create_video_subfolder", _fake_subfolder)
    monkeypatch.setattr(orch, "resolve_adapter", lambda url: _FakeAdapter())
    monkeypatch.setattr(orch.clip_service, "probe_safe_end", lambda path: 999.0)
    monkeypatch.setattr(
        orch.clip_service, "extract_chapter_to_disk",
        lambda video_path, start, end, out_clip, out_audio: (
            Path(out_clip).parent.mkdir(parents=True, exist_ok=True),
            Path(out_clip).write_bytes(b"\x00"),
            Path(out_audio).write_bytes(b"\x00"),
            (out_clip, out_audio),
        )[-1],
    )
    monkeypatch.setattr(
        orch.transcription_service, "transcribe_to_words",
        lambda audio_path, language="en": list(words),
    )
    monkeypatch.setattr(orch.render_service, "render_clip", _fake_render)
    monkeypatch.setattr(orch.subtitle_image_service, "render_to_path", _fake_render_to_path)
    monkeypatch.setattr(orch.settings, "max_parallel_chapters", 1)

    def _record_enhance(in_path, out_path, **kwargs):
        enhance_calls.append((in_path, out_path, kwargs))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x00")
        return out_path

    def _record_hook(transcript, **kwargs):
        hook_calls.append((transcript, kwargs))
        return hook_return

    monkeypatch.setattr(orch.audio_enhance_service, "enhance", _record_enhance)
    monkeypatch.setattr(orch.ai_hook_service, "generate_hook", _record_hook)


async def _drive_pipeline(
    tmp_path: Path,
    pipeline_options: PipelineOptions,
) -> tuple[list[Event], JobStore]:
    bus = AsyncEventBus()
    store = JobStore()

    state = JobState(
        job_id="job-w13",
        url="https://www.youtube.com/watch?v=fake",
        source="youtube",
        download_path=str(tmp_path),
        pipeline_options=pipeline_options,
    )
    await store.create(state)

    received: list[Event] = []

    async def collect():
        async for event in bus.subscribe(job_id="job-w13"):
            received.append(event)
            if event.type in (EventType.JOB_COMPLETED, EventType.JOB_FAILED):
                return

    collector = asyncio.create_task(collect())
    runner = asyncio.create_task(orch.run_orchestrator(bus, store))

    # Allow subscribers to register before publishing.
    for _ in range(5):
        await asyncio.sleep(0.01)

    await bus.publish(
        Event(
            type=EventType.VIDEO_REQUESTED,
            job_id="job-w13",
            payload={
                "url": state.url,
                "download_path": state.download_path,
                "caption_format": "srt",
                "target_aspect_ratio": 9 / 16,
                "pipeline_options": pipeline_options.model_dump(),
            },
        )
    )

    await asyncio.wait_for(collector, timeout=10)
    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass

    return received, store


# ── audio_enhance gate ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audio_enhance_runs_when_enabled(tmp_path, monkeypatch):
    """audio_enhance=True → service is called and downstream audio path is the enhanced file."""
    enhance_calls: list[tuple[Any, ...]] = []
    hook_calls: list[tuple[Any, ...]] = []
    words = [WordTiming("hello", 0.0, 0.5), WordTiming("world", 0.5, 1.0)]
    _install_pipeline_doubles(
        monkeypatch, words=words,
        enhance_calls=enhance_calls, hook_calls=hook_calls,
    )

    opts = PipelineOptions(audio_enhance=True, ai_hook=False, filler_removal=False)
    events, store = await _drive_pipeline(tmp_path, opts)

    assert events[-1].type is EventType.JOB_COMPLETED
    assert len(enhance_calls) == 1, "audio_enhance_service.enhance should fire once per chapter"

    in_path, out_path, kwargs = enhance_calls[0]
    assert in_path.endswith("chapter_0.wav")
    assert out_path.endswith("chapter_0_enhanced.wav")
    assert kwargs.get("provider") == orch.settings.audio_enhance_provider

    # The chapter record should now point at the enhanced audio.
    final = await store.get("job-w13")
    chapter = final.chapters[0]
    assert chapter.audio_path == out_path


@pytest.mark.asyncio
async def test_audio_enhance_skipped_when_disabled(tmp_path, monkeypatch):
    """audio_enhance=False → service NOT called, STAGE_SKIPPED emitted."""
    enhance_calls: list[tuple[Any, ...]] = []
    hook_calls: list[tuple[Any, ...]] = []
    words = [WordTiming("hi", 0.0, 0.4)]
    _install_pipeline_doubles(
        monkeypatch, words=words,
        enhance_calls=enhance_calls, hook_calls=hook_calls,
    )

    opts = PipelineOptions(audio_enhance=False, ai_hook=False, filler_removal=False)
    events, _store = await _drive_pipeline(tmp_path, opts)

    assert events[-1].type is EventType.JOB_COMPLETED
    assert enhance_calls == []
    skipped = {e.payload.get("stage_id") for e in events if e.type == EventType.STAGE_SKIPPED}
    assert "audio_enhance" in skipped


@pytest.mark.asyncio
async def test_audio_enhance_failure_falls_back_gracefully(tmp_path, monkeypatch):
    """Enhancement errors must not fail the job — the original audio is reused."""
    hook_calls: list[tuple[Any, ...]] = []
    words = [WordTiming("hello", 0.0, 0.5)]
    _install_pipeline_doubles(
        monkeypatch, words=words,
        enhance_calls=[], hook_calls=hook_calls,
    )

    def _boom(in_path, out_path, **kwargs):
        raise RuntimeError("ffmpeg unavailable")

    monkeypatch.setattr(orch.audio_enhance_service, "enhance", _boom)

    opts = PipelineOptions(audio_enhance=True, ai_hook=False, filler_removal=False)
    events, store = await _drive_pipeline(tmp_path, opts)

    assert events[-1].type is EventType.JOB_COMPLETED
    final = await store.get("job-w13")
    # Original (non-enhanced) audio path retained.
    assert final.chapters[0].audio_path.endswith("chapter_0.wav")


# ── ai_hook gate ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_hook_runs_when_enabled(tmp_path, monkeypatch):
    """ai_hook=True → service is called with the transcript; hook stored on clip."""
    enhance_calls: list[tuple[Any, ...]] = []
    hook_calls: list[tuple[Any, ...]] = []
    words = [
        WordTiming("this", 0.0, 0.2),
        WordTiming("is", 0.2, 0.3),
        WordTiming("a", 0.3, 0.4),
        WordTiming("test", 0.4, 0.7),
    ]
    _install_pipeline_doubles(
        monkeypatch, words=words,
        enhance_calls=enhance_calls, hook_calls=hook_calls,
        hook_return="One weird trick.",
    )

    opts = PipelineOptions(audio_enhance=False, ai_hook=True, filler_removal=False)
    events, store = await _drive_pipeline(tmp_path, opts)

    assert events[-1].type is EventType.JOB_COMPLETED
    assert len(hook_calls) == 1
    transcript, _kwargs = hook_calls[0]
    assert "test" in transcript

    clips = await store.list_clips(job_id="job-w13")
    assert clips, "should have produced at least one clip"
    assert clips[0].get("ai_hook_text") == "One weird trick."


@pytest.mark.asyncio
async def test_ai_hook_skipped_when_disabled(tmp_path, monkeypatch):
    """ai_hook=False → service NOT called, STAGE_SKIPPED emitted, no ai_hook_text on clip."""
    enhance_calls: list[tuple[Any, ...]] = []
    hook_calls: list[tuple[Any, ...]] = []
    words = [WordTiming("hi", 0.0, 0.4)]
    _install_pipeline_doubles(
        monkeypatch, words=words,
        enhance_calls=enhance_calls, hook_calls=hook_calls,
    )

    opts = PipelineOptions(audio_enhance=False, ai_hook=False, filler_removal=False)
    events, store = await _drive_pipeline(tmp_path, opts)

    assert events[-1].type is EventType.JOB_COMPLETED
    assert hook_calls == []
    skipped = {e.payload.get("stage_id") for e in events if e.type == EventType.STAGE_SKIPPED}
    assert "ai_hook" in skipped

    clips = await store.list_clips(job_id="job-w13")
    assert clips
    assert not clips[0].get("ai_hook_text")


@pytest.mark.asyncio
async def test_ai_hook_disabled_when_transcription_off(tmp_path, monkeypatch):
    """Server-side safety net: transcription=False forces ai_hook off."""
    enhance_calls: list[tuple[Any, ...]] = []
    hook_calls: list[tuple[Any, ...]] = []
    _install_pipeline_doubles(
        monkeypatch, words=[],
        enhance_calls=enhance_calls, hook_calls=hook_calls,
        hook_return="should-not-run",
    )

    # User sends ai_hook=True but transcription=False — server should override.
    opts = PipelineOptions(transcription=False, ai_hook=True)
    events, _store = await _drive_pipeline(tmp_path, opts)

    assert events[-1].type is EventType.JOB_COMPLETED
    assert hook_calls == []


# ── filler_removal gate ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_filler_removal_strips_filler_words(tmp_path, monkeypatch):
    """filler_removal=True → fillers dropped, clip.transcript reflects cleaned text."""
    enhance_calls: list[tuple[Any, ...]] = []
    hook_calls: list[tuple[Any, ...]] = []
    words = [
        WordTiming("um", 0.0, 0.1),
        WordTiming("hello", 0.1, 0.4),
        WordTiming("uh", 0.4, 0.5),
        WordTiming("world", 0.5, 0.9),
    ]
    _install_pipeline_doubles(
        monkeypatch, words=words,
        enhance_calls=enhance_calls, hook_calls=hook_calls,
    )

    opts = PipelineOptions(audio_enhance=False, ai_hook=False, filler_removal=True)
    events, store = await _drive_pipeline(tmp_path, opts)

    assert events[-1].type is EventType.JOB_COMPLETED
    clips = await store.list_clips(job_id="job-w13")
    assert clips
    transcript = clips[0].get("transcript")
    assert transcript == "hello world", f"expected fillers stripped, got {transcript!r}"


@pytest.mark.asyncio
async def test_filler_removal_skipped_when_disabled(tmp_path, monkeypatch):
    """filler_removal=False → fillers retained, STAGE_SKIPPED emitted."""
    enhance_calls: list[tuple[Any, ...]] = []
    hook_calls: list[tuple[Any, ...]] = []
    words = [
        WordTiming("um", 0.0, 0.1),
        WordTiming("hello", 0.1, 0.4),
    ]
    _install_pipeline_doubles(
        monkeypatch, words=words,
        enhance_calls=enhance_calls, hook_calls=hook_calls,
    )

    opts = PipelineOptions(audio_enhance=False, ai_hook=False, filler_removal=False)
    events, store = await _drive_pipeline(tmp_path, opts)

    assert events[-1].type is EventType.JOB_COMPLETED
    skipped = {e.payload.get("stage_id") for e in events if e.type == EventType.STAGE_SKIPPED}
    assert "filler_removal" in skipped

    clips = await store.list_clips(job_id="job-w13")
    assert clips[0].get("transcript") == "um hello"


# ── full happy path ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_w13_gates_on_together(tmp_path, monkeypatch):
    """audio_enhance + ai_hook + filler_removal all on → every service fires, job completes."""
    enhance_calls: list[tuple[Any, ...]] = []
    hook_calls: list[tuple[Any, ...]] = []
    words = [
        WordTiming("um", 0.0, 0.1),
        WordTiming("ship", 0.1, 0.4),
        WordTiming("it", 0.4, 0.6),
    ]
    _install_pipeline_doubles(
        monkeypatch, words=words,
        enhance_calls=enhance_calls, hook_calls=hook_calls,
        hook_return="Ship it.",
    )

    opts = PipelineOptions(audio_enhance=True, ai_hook=True, filler_removal=True)
    events, store = await _drive_pipeline(tmp_path, opts)

    assert events[-1].type is EventType.JOB_COMPLETED
    assert len(enhance_calls) == 1
    assert len(hook_calls) == 1
    # No STAGE_SKIPPED for any of the three new gates.
    skipped = {e.payload.get("stage_id") for e in events if e.type == EventType.STAGE_SKIPPED}
    assert "audio_enhance" not in skipped
    assert "ai_hook" not in skipped
    assert "filler_removal" not in skipped

    clips = await store.list_clips(job_id="job-w13")
    assert clips[0].get("transcript") == "ship it"
    assert clips[0].get("ai_hook_text") == "Ship it."
