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


def _fake_subfolder(
    download_path: str, url: str, platform_id: str = "video", job_id: str | None = None
):
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


# Recorders for the fakes below; reset by _run_pipeline.
_CALLS: dict[str, list] = {}


def _fake_extract_audio(src, start, duration, wav_path):
    _CALLS.setdefault("extract_audio", []).append((src, start, duration, wav_path))
    Path(wav_path).parent.mkdir(parents=True, exist_ok=True)
    Path(wav_path).write_bytes(b"\x00")
    return wav_path


def _fake_render(video_path, output_path, *args, **kwargs):
    _CALLS.setdefault("render", []).append((video_path, output_path, args, kwargs))
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(b"\x00")
    return output_path


async def _run_pipeline(
    tmp_path,
    pipeline_options: PipelineOptions,
    monkeypatch,
    extract_audio=None,
    adapter=None,
    enhance=None,
    render=None,
):
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
    monkeypatch.setattr(orch, "resolve_adapter", lambda url: adapter or _FakeAdapter())
    monkeypatch.setattr(orch.clip_service, "probe_safe_end", lambda path: 999.0)
    _CALLS.clear()
    monkeypatch.setattr(
        orch.clip_service, "extract_audio", extract_audio or _fake_extract_audio
    )
    from app.services.transcription_service import WordTiming
    monkeypatch.setattr(
        orch.transcription_service, "transcribe_to_words",
        lambda audio_path, **_: [
            WordTiming("hello", 0.0, 0.5),
            WordTiming("world", 0.5, 1.0),
        ],
    )
    monkeypatch.setattr(orch.render_service, "render_clip", render or _fake_render)
    monkeypatch.setattr(orch.settings, "max_parallel_chapters", 1)
    # Avoid real network/ffmpeg calls for the new W1/W2 stages
    monkeypatch.setattr(orch.ai_hook_service, "generate_hook", lambda *a, **k: "")
    monkeypatch.setattr(
        orch.audio_enhance_service, "enhance",
        enhance or (lambda in_path, out_path, **k: (
            Path(out_path).parent.mkdir(parents=True, exist_ok=True),
            Path(out_path).write_bytes(b"\x00"),
            out_path,
        )[-1]),
    )

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
    """All toggles on → identical event chain to today's full pipeline (regression).

    Note: ai_hook and filler_removal default to False (opt-in per W1.7/W2.5),
    so "all on" must turn them on explicitly. The original regression intent
    is preserved: no STAGE_SKIPPED events when every gated stage is enabled,
    except B-roll, which also needs a provider: with the default
    ``broll_provider=none`` each chapter reports StageSkipped(broll,
    "no provider") (T012).
    """
    opts = PipelineOptions(ai_hook=True, filler_removal=True)
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

    # No STAGE_SKIPPED events when all on, but the provider-less B-roll stage
    skip_events = [e for e in events if e.type == EventType.STAGE_SKIPPED]
    rendered = [e.payload["chapter_index"] for e in events if e.type == EventType.CLIP_RENDERED]
    assert rendered == [0]
    assert sorted(
        (e.payload["stage_id"], e.payload["chapter_index"], e.payload["reason"])
        for e in skip_events
    ) == [("broll", i, "no provider") for i in sorted(rendered)]

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


# ── P1: audio extraction from the source replaces extract_chapter_to_disk ─────
# Before P1 a chapter mp4 + wav were cut whenever render was on (and, for
# transcription only, also when render was off), and render_clip re-read that
# chapter mp4. Now ONLY the wav is cut, ONLY when transcription is on, and
# render_clip reads the SOURCE directly. CHAPTER_CLIP_EXTRACTED is still
# emitted while render is on (the web UI's "Extract clips" stage counts it),
# now with clip_path=None.


def _clip_extracted(events):
    return [e for e in events if e.type == EventType.CHAPTER_CLIP_EXTRACTED]


@pytest.mark.asyncio
async def test_default_extracts_chapter_audio_and_renders_from_source(tmp_path, monkeypatch):
    events, store = await _run_pipeline(tmp_path, PipelineOptions(), monkeypatch)
    final = await store.get("job-gate")
    source = final.video_path

    assert len(_CALLS["extract_audio"]) == 1
    src, start, duration, wav = _CALLS["extract_audio"][0]
    assert (src, start, duration) == (source, 0.0, 10.0)
    assert wav.endswith("chapter_0.wav")

    [extracted] = _clip_extracted(events)
    assert extracted.payload == {"chapter_index": 0, "clip_path": None, "audio_path": wav}

    [(video_path, _out, args, kwargs)] = _CALLS["render"]
    assert video_path == source  # no intermediate chapter mp4
    assert args[:2] == (0.0, 10.0)  # the chapter window in source time
    assert [w.word for w in kwargs["word_timings"]] == ["hello", "world"]

    chapter = final.chapters[0]
    assert chapter.clip_path is None
    assert chapter.audio_path.endswith("chapter_0_enhanced.wav")  # audio_enhance on


@pytest.mark.asyncio
async def test_transcription_off_render_on_skips_extraction_but_emits_stage(tmp_path, monkeypatch):
    events, _ = await _run_pipeline(tmp_path, PipelineOptions(transcription=False), monkeypatch)
    assert "extract_audio" not in _CALLS
    [extracted] = _clip_extracted(events)
    assert extracted.payload == {"chapter_index": 0, "clip_path": None, "audio_path": None}
    [(_src, _out, _args, kwargs)] = _CALLS["render"]
    assert kwargs["word_timings"] == []
    types = [e.type for e in events]
    assert types[-1] is EventType.JOB_COMPLETED


@pytest.mark.asyncio
async def test_render_off_transcription_on_extracts_audio_without_stage_event(tmp_path, monkeypatch):
    events, _ = await _run_pipeline(tmp_path, PipelineOptions(render=False), monkeypatch)
    assert len(_CALLS["extract_audio"]) == 1
    assert _clip_extracted(events) == []
    assert "render" not in _CALLS
    assert EventType.CHAPTER_TRANSCRIBED in [e.type for e in events]


@pytest.mark.asyncio
async def test_all_off_extracts_nothing(tmp_path, monkeypatch):
    opts = PipelineOptions(transcription=False, render=False)
    events, _ = await _run_pipeline(tmp_path, opts, monkeypatch)
    assert "extract_audio" not in _CALLS
    assert _clip_extracted(events) == []


@pytest.mark.asyncio
async def test_subtitle_pngs_are_no_longer_rendered_by_the_orchestrator(tmp_path, monkeypatch):
    """Captions are burned in by render_clip; the old per-caption PNG loop is gone."""
    events, store = await _run_pipeline(tmp_path, PipelineOptions(), monkeypatch)
    assert EventType.SUBTITLE_IMAGE_RENDERED not in [e.type for e in events]
    assert (await store.get("job-gate")).chapters[0].image_paths == []


@pytest.mark.asyncio
async def test_source_without_audio_skips_transcription_words(tmp_path, monkeypatch):
    """A source with no audio stream: nothing to transcribe, the job still completes
    (before P1 the missing wav reached the transcriber and failed the job)."""
    calls = []

    def no_audio(src, start, duration, wav_path):
        calls.append(wav_path)
        return None

    events, _ = await _run_pipeline(
        tmp_path, PipelineOptions(), monkeypatch, extract_audio=no_audio
    )
    assert len(calls) == 1
    [extracted] = _clip_extracted(events)
    assert extracted.payload["audio_path"] is None
    # the helper's transcriber stub returns "hello world" if it is ever called
    transcribed = [e for e in events if e.type == EventType.CHAPTER_TRANSCRIBED]
    assert [e.payload["text"] for e in transcribed] == [""]
    assert [e.type for e in events][-1] is EventType.JOB_COMPLETED


class _OffsetChapterAdapter(_FakeAdapter):
    """One chapter that does NOT start at 0: [2.5, 7.0)."""

    def extract_chapters(self, info: dict) -> list[Chapter]:
        return [Chapter(index=0, title="Mid", start=2.5, end=7.0)]


@pytest.mark.asyncio
async def test_offset_chapter_uses_source_time_window(tmp_path, monkeypatch):
    """extract_audio gets (start, duration); render_clip gets (start, end) — both
    in SOURCE time, the same window, since neither reads an intermediate clip."""
    events, store = await _run_pipeline(
        tmp_path, PipelineOptions(), monkeypatch, adapter=_OffsetChapterAdapter()
    )
    source = (await store.get("job-gate")).video_path
    [(src, start, duration, _wav)] = _CALLS["extract_audio"]
    assert (src, start, duration) == (source, 2.5, 4.5)
    [(video_path, _out, args, _kwargs)] = _CALLS["render"]
    assert (video_path, args[:2]) == (source, (2.5, 7.0))
    assert [e.type for e in events][-1] is EventType.JOB_COMPLETED


@pytest.mark.asyncio
async def test_ffmpeg_stages_run_with_a_cancel_hook(tmp_path, monkeypatch):
    """extract_audio, audio_enhance and render run via to_thread_cancellable,
    so cancelling the job (or a wait_for timeout) kills their ffmpeg."""
    from app.services import ffmpeg_tools

    hooks: dict[str, object] = {}

    def extract(src, start, duration, wav_path):
        hooks["extract_audio"] = ffmpeg_tools.current_cancel_event()
        return _fake_extract_audio(src, start, duration, wav_path)

    def enhance(in_path, out_path, **kwargs):
        hooks["audio_enhance"] = ffmpeg_tools.current_cancel_event()
        Path(out_path).write_bytes(b"\x00")
        return out_path

    def render(video_path, output_path, *args, **kwargs):
        hooks["render"] = ffmpeg_tools.current_cancel_event()
        return _fake_render(video_path, output_path, *args, **kwargs)

    events, _ = await _run_pipeline(
        tmp_path,
        PipelineOptions(),
        monkeypatch,
        extract_audio=extract,
        enhance=enhance,
        render=render,
    )
    assert [e.type for e in events][-1] is EventType.JOB_COMPLETED
    assert set(hooks) == {"extract_audio", "audio_enhance", "render"}
    assert all(h is not None for h in hooks.values()), hooks
