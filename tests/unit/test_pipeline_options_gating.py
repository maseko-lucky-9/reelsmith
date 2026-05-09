"""Orchestrator gating tests — verify stages are skipped/called based on PipelineOptions."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import JobStore
from app.domain.events import Event, EventType
from app.domain.models import JobState, PipelineOptions
from app.workers import orchestrator as orch
from app.services.platforms.base import Chapter, DownloadResult


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


async def _run_pipeline(tmp_path, pipeline_options: PipelineOptions, monkeypatch):
    """Helper: runs the full pipeline with given PipelineOptions and returns collected events."""
    bus = AsyncEventBus()
    store = JobStore()

    state = JobState(
        job_id="job-gate",
        url="https://www.youtube.com/watch?v=fake",
        source="youtube",
        download_path=str(tmp_path),
        pipeline_options=pipeline_options,
    )
    await store.create(state)

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
    from app.services.transcription_service import WordTiming
    monkeypatch.setattr(
        orch.transcription_service, "transcribe_to_words",
        lambda audio_path, language="en": [
            WordTiming("hello", 0.0, 0.5),
            WordTiming("world", 0.5, 1.0),
        ],
    )
    monkeypatch.setattr(orch.render_service, "render_clip", _fake_render)
    monkeypatch.setattr(orch.subtitle_image_service, "render_to_path", _fake_render_to_path)
    monkeypatch.setattr(orch.settings, "max_parallel_chapters", 1)

    received: list[Event] = []

    async def collect():
        async for event in bus.subscribe(job_id="job-gate"):
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
            job_id="job-gate",
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
    orchestrator.cancel()
    try:
        await orchestrator
    except asyncio.CancelledError:
        pass

    return received, store


@pytest.mark.asyncio
async def test_transcription_off_skips_transcribe_and_captions(tmp_path, monkeypatch):
    """transcription=False → transcribe NOT called, captions NOT called, STAGE_SKIPPED emitted."""
    opts = PipelineOptions(transcription=False)
    # captions should be auto-disabled by server-side safety net
    events, store = await _run_pipeline(tmp_path, opts, monkeypatch)
    types = [e.type for e in events]

    assert EventType.CHAPTER_TRANSCRIBED not in types
    assert EventType.CAPTIONS_GENERATED not in types

    skip_events = [e for e in events if e.type == EventType.STAGE_SKIPPED]
    skip_stages = [e.payload.get("stage_id") for e in skip_events]
    assert "transcribe" in skip_stages
    assert "caption" in skip_stages

    assert types[-1] is EventType.JOB_COMPLETED


@pytest.mark.asyncio
async def test_render_off_skips_clip_and_thumbnail(tmp_path, monkeypatch):
    """render=False → no per-chapter clip extraction for rendering, no thumbnail, STAGE_SKIPPED emitted."""
    opts = PipelineOptions(render=False)
    events, store = await _run_pipeline(tmp_path, opts, monkeypatch)
    types = [e.type for e in events]

    assert EventType.CLIP_RENDERED not in types

    skip_events = [e for e in events if e.type == EventType.STAGE_SKIPPED]
    skip_stages = [e.payload.get("stage_id") for e in skip_events]
    assert "render" in skip_stages
    assert "thumbnail" in skip_stages
    assert "reframe" in skip_stages
    assert "broll" in skip_stages

    assert types[-1] is EventType.JOB_COMPLETED


@pytest.mark.asyncio
async def test_all_off_only_download_and_folder(tmp_path, monkeypatch):
    """All toggles off → only download + folder + manifest happen."""
    opts = PipelineOptions(
        transcription=False,
        captions=False,
        render=False,
        segment_proposer=False,
        reframe=False,
        broll=False,
        thumbnail=False,
    )
    events, store = await _run_pipeline(tmp_path, opts, monkeypatch)
    types = [e.type for e in events]

    assert EventType.FOLDER_CREATED in types
    assert EventType.VIDEO_DOWNLOADED in types
    assert EventType.CHAPTERS_DETECTED in types

    assert EventType.CHAPTER_TRANSCRIBED not in types
    assert EventType.CAPTIONS_GENERATED not in types
    assert EventType.CLIP_RENDERED not in types
    assert EventType.THUMBNAIL_GENERATED not in types

    assert types[-1] is EventType.JOB_COMPLETED


@pytest.mark.asyncio
async def test_all_on_identical_to_full_pipeline(tmp_path, monkeypatch):
    """All toggles on → identical event chain to today's full pipeline (regression)."""
    opts = PipelineOptions()  # all defaults = True
    events, store = await _run_pipeline(tmp_path, opts, monkeypatch)
    types = [e.type for e in events]

    assert EventType.FOLDER_CREATED in types
    assert EventType.VIDEO_DOWNLOADED in types
    assert EventType.CHAPTERS_DETECTED in types
    assert EventType.CHAPTER_CLIP_EXTRACTED in types
    assert EventType.CHAPTER_TRANSCRIBED in types
    assert EventType.CAPTIONS_GENERATED in types
    assert EventType.CLIP_RENDERED in types
    assert types[-1] is EventType.JOB_COMPLETED

    # No STAGE_SKIPPED events when all on
    skip_events = [e for e in events if e.type == EventType.STAGE_SKIPPED]
    assert len(skip_events) == 0

    final = await store.get("job-gate")
    assert final.status == "completed"


@pytest.mark.asyncio
async def test_render_off_forces_dependent_flags_off(tmp_path, monkeypatch):
    """Server-side safety net: render=False forces reframe/broll/thumbnail=False."""
    # User sends render=False but reframe=True — server should override
    opts = PipelineOptions(render=False, reframe=True, broll=True, thumbnail=True)
    events, store = await _run_pipeline(tmp_path, opts, monkeypatch)
    types = [e.type for e in events]

    assert EventType.CLIP_RENDERED not in types
    assert EventType.THUMBNAIL_GENERATED not in types

    skip_events = [e for e in events if e.type == EventType.STAGE_SKIPPED]
    skip_stages = [e.payload.get("stage_id") for e in skip_events]
    assert "render" in skip_stages


@pytest.mark.asyncio
async def test_captions_off_render_on_produces_clip_without_subs(tmp_path, monkeypatch):
    """captions=False + render=True → clip rendered but no captions generated."""
    opts = PipelineOptions(captions=False)
    events, store = await _run_pipeline(tmp_path, opts, monkeypatch)
    types = [e.type for e in events]

    # Transcription still runs (transcription=True by default)
    assert EventType.CHAPTER_TRANSCRIBED in types
    # Captions skipped
    assert EventType.CAPTIONS_GENERATED not in types
    # Render still happens
    assert EventType.CLIP_RENDERED in types

    skip_events = [e for e in events if e.type == EventType.STAGE_SKIPPED]
    skip_stages = [e.payload.get("stage_id") for e in skip_events]
    assert "caption" in skip_stages

    assert types[-1] is EventType.JOB_COMPLETED


@pytest.mark.asyncio
async def test_thumbnail_off_render_on(tmp_path, monkeypatch):
    """thumbnail=False + render=True → clip rendered, no thumbnail."""
    opts = PipelineOptions(thumbnail=False)
    events, store = await _run_pipeline(tmp_path, opts, monkeypatch)
    types = [e.type for e in events]

    assert EventType.CLIP_RENDERED in types
    assert EventType.THUMBNAIL_GENERATED not in types

    skip_events = [e for e in events if e.type == EventType.STAGE_SKIPPED]
    skip_stages = [e.payload.get("stage_id") for e in skip_events]
    assert "thumbnail" in skip_stages
