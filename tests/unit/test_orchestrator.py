"""Verify the orchestrator emits the expected event chain when its services are stubbed."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import JobStore
from app.domain.events import Event, EventType
from app.domain.models import JobState
from app.workers import orchestrator as orch


def _fake_subfolder(download_path: str, url: str):
    base = Path(download_path) / "vid"
    clips = base / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    return str(base), str(clips)


def _fake_download(url: str, destination_folder: str):
    video_path = str(Path(destination_folder) / "video.mp4")
    Path(video_path).write_bytes(b"\x00")
    info = {
        "title": "Test",
        "duration": 12.0,
        "chapters": [
            {"title": "Intro", "start_time": 0.0, "end_time": 6.0},
            {"title": "Outro", "start_time": 6.0, "end_time": 12.0},
        ],
    }
    return video_path, info


def _fake_extract(*args, **kwargs):
    return None


def _fake_render(video_path, output_path, *args, **kwargs):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(b"\x00")
    return output_path


def _fake_render_to_path(text, videosize, path, font_size=50):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"\x00")
    return path


@pytest.mark.asyncio
async def test_orchestrator_emits_full_event_chain(tmp_path, monkeypatch):
    bus = AsyncEventBus()
    store = JobStore()

    state = JobState(job_id="job-1", url="https://example.com/v", download_path=str(tmp_path))
    await store.create(state)

    monkeypatch.setattr(orch.folder_service, "create_video_subfolder", _fake_subfolder)
    monkeypatch.setattr(orch.download_service, "download_video", _fake_download)
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
        async for event in bus.subscribe(job_id="job-1"):
            received.append(event)
            if event.type is EventType.JOB_COMPLETED:
                return

    collector = asyncio.create_task(collect())
    orchestrator = asyncio.create_task(orch.run_orchestrator(bus, store))

    # Let both subscribers register before publishing
    for _ in range(5):
        await asyncio.sleep(0.01)

    # Trigger the pipeline
    await bus.publish(
        Event(
            type=EventType.VIDEO_REQUESTED,
            job_id="job-1",
            payload={
                "url": state.url,
                "download_path": state.download_path,
                "caption_format": "srt",
                "target_aspect_ratio": 9 / 16,
            },
        )
    )

    await asyncio.wait_for(collector, timeout=10)
    orchestrator.cancel()
    try:
        await orchestrator
    except asyncio.CancelledError:
        pass

    types = [e.type for e in received]
    assert types[0] is EventType.VIDEO_REQUESTED
    assert EventType.FOLDER_CREATED in types
    assert EventType.VIDEO_DOWNLOADED in types
    assert EventType.CHAPTERS_DETECTED in types
    assert types.count(EventType.CHAPTER_CLIP_EXTRACTED) == 2
    assert types.count(EventType.CHAPTER_TRANSCRIBED) == 2
    assert types.count(EventType.CAPTIONS_GENERATED) == 2
    assert types.count(EventType.CLIP_RENDERED) == 2
    assert types[-1] is EventType.JOB_COMPLETED

    final = await store.get("job-1")
    assert final.status == "completed"
    assert len(final.output_paths) == 2
