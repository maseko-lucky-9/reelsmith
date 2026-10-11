"""Orchestrator → transcription wiring: job language and chapter-scaled timeout."""

from __future__ import annotations

import asyncio
import time
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
from tests.unit.fake_whisper import FakeModelFactory, install

JOB_ID = "job-lang"
CHAPTER_SECONDS = 6.0
# Only the transcription stage runs; everything it doesn't need is off.
TRANSCRIBE_ONLY = PipelineOptions(
    transcription=True,
    captions=False,
    render=False,
    segment_proposer=False,
    audio_enhance=False,
)


class _OneChapterAdapter:
    platform_id = "youtube"

    def download(self, url: str, destination_folder: str) -> DownloadResult:
        video_path = str(Path(destination_folder) / "video.mp4")
        Path(video_path).write_bytes(b"\x00")
        info = {"title": "T", "duration": CHAPTER_SECONDS}
        return DownloadResult(
            video_path=video_path,
            info=info,
            title="T",
            duration=CHAPTER_SECONDS,
            source=self.platform_id,
        )

    def extract_chapters(self, info: dict[str, Any]) -> list[Chapter]:
        return [Chapter(index=0, title="Ch", start=0.0, end=CHAPTER_SECONDS)]


def _fake_subfolder(
    download_path: str, url: str, platform_id: str = "video", job_id: str | None = None
):
    clips = Path(download_path) / "vid" / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    return str(clips.parent), str(clips)


def _fake_extract_audio(src: str, start: float, duration: float, wav_path: str) -> str:
    Path(wav_path).write_bytes(b"\x00")
    return wav_path


@pytest.fixture(autouse=True)
def _pipeline_doubles(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Keep exports in tmp: a developer .env may point export_base_folder at a
    # synced share.
    monkeypatch.setattr(orch.settings, "export_base_folder", str(tmp_path / "exports"))
    monkeypatch.setattr(orch.folder_service, "create_video_subfolder", _fake_subfolder)
    monkeypatch.setattr(orch, "resolve_adapter", lambda url: _OneChapterAdapter())
    monkeypatch.setattr(orch.clip_service, "probe_safe_end", lambda path: 999.0)
    monkeypatch.setattr(orch.clip_service, "extract_audio", _fake_extract_audio)
    monkeypatch.setattr(orch.settings, "max_parallel_chapters", 1)


async def _run_job(tmp_path: Path, **payload: Any) -> tuple[list[Event], JobStore]:
    bus = AsyncEventBus()
    store = JobStore()
    url = "https://www.youtube.com/watch?v=fake"
    await store.create(
        JobState(job_id=JOB_ID, url=url, source="youtube", download_path=str(tmp_path))
    )
    received: list[Event] = []

    async def collect() -> None:
        async for event in bus.subscribe(job_id=JOB_ID):
            received.append(event)
            if event.type in (EventType.JOB_COMPLETED, EventType.JOB_FAILED):
                return

    collector = asyncio.create_task(collect())
    runner = asyncio.create_task(orch.run_orchestrator(bus, store))
    for _ in range(5):
        await asyncio.sleep(0.01)
    await bus.publish(
        Event(
            type=EventType.VIDEO_REQUESTED,
            job_id=JOB_ID,
            payload={
                "url": url,
                "download_path": str(tmp_path),
                "pipeline_options": TRANSCRIBE_ONLY.model_dump(),
                **payload,
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


def _record_language(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    seen: list[str | None] = []

    def fake(
        audio_path: str, language: str | None = None, **_: Any
    ) -> list[WordTiming]:
        seen.append(language)
        return [WordTiming("hi", 0.0, 0.4)]

    monkeypatch.setattr(orch.transcription_service, "transcribe_to_words", fake)
    return seen


async def test_job_language_reaches_transcription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _record_language(monkeypatch)

    events, _ = await _run_job(tmp_path, language="pt-BR")

    assert events[-1].type is EventType.JOB_COMPLETED
    assert seen == ["pt-BR"]


async def test_job_without_language_leaves_the_default_to_the_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _record_language(monkeypatch)

    events, _ = await _run_job(tmp_path)

    assert events[-1].type is EventType.JOB_COMPLETED
    assert seen == [None]


async def test_region_tagged_job_language_transcribes_with_whisper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: "en-US" from the job request through a model that rejects it raw."""
    factory: FakeModelFactory = install(monkeypatch)

    events, store = await _run_job(tmp_path, language="en-US")

    assert events[-1].type is EventType.JOB_COMPLETED, events[-1].payload
    assert factory.model.transcribe_calls[-1]["language"] == "en"
    chapter = (await store.get(JOB_ID)).chapters[0]
    assert chapter.transcript == "w0 w1"


async def test_transcription_timeout_scales_with_chapter_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orch.settings, "transcription_timeout_seconds", 0.1)

    def slow(audio_path: str, *, on_start: Any = None, **_: Any) -> list[WordTiming]:
        if on_start is not None:
            on_start()
        time.sleep(0.3)  # longer than the setting, far shorter than the chapter
        return [WordTiming("hi", 0.0, 0.4)]

    monkeypatch.setattr(orch.transcription_service, "transcribe_to_words", slow)

    events, _ = await _run_job(tmp_path, language="en")

    assert events[-1].type is EventType.JOB_COMPLETED, events[-1].payload
