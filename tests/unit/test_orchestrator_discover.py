"""Clip discovery for sources without chapters (FR-009, T011 wiring).

When a source has no chapters, the job's ``segment_proposer``, ``transcription``
and ``segment_mode == "auto"`` are on, and ``settings.segment_provider`` is not
``"chapter"``, the orchestrator transcribes the whole source once, scores
candidate windows with the configured proposer, keeps the best and renders
each as a chapter. Any other combination keeps today's single "Full Video"
chapter.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import InMemoryJobStore, SqlJobStore
from app.db import models as _models  # noqa: F401 — registers tables on Base
from app.db.base import Base
from app.domain.events import Event, EventType
from app.domain.models import JobState, PipelineOptions
from app.services import segment_rerank
from app.services.platforms.base import Chapter, DownloadResult
from app.services.segment_proposer import ProposedSegment
from app.services.segment_proposer import (
    get_segment_proposer as real_get_segment_proposer,
)
from app.services.transcription_service import WordTiming
from app.workers import orchestrator as orch

JOB_ID = "job-discover"
URL = "https://www.youtube.com/watch?v=nochapters1"
# 6 minutes: a budget of 3 clips and a 180 s coverage cap, so the selection
# rules let both Alpha and Beta through (the rules are unit-tested on their own).
SOURCE_SECONDS = 360.0
# One word a second: w0 at 0.0-0.6 ... w359 at 359.0-359.6.
SOURCE_WORDS = [WordTiming(f"w{i}", float(i), i + 0.6) for i in range(360)]


def _segments() -> list[ProposedSegment]:
    return [
        ProposedSegment(
            start=10.3,
            end=39.3,
            title="Alpha",
            summary="alpha summary",
            score=30,
            score_breakdown={"hook": 0.6, "value": 0.2},
        ),
        # Overlaps Alpha and scores lower: not picked.
        ProposedSegment(start=15.0, end=45.0, title="Overlap", score=25),
        ProposedSegment(
            start=50.0,
            end=80.0,
            title="Beta",
            summary="beta summary",
            score=20,
            score_breakdown={"hook": 0.1, "value": 0.4},
        ),
        # Below 60% of the best (ceil(0.6 * 30) = 18): dropped.
        ProposedSegment(start=85.0, end=115.0, title="Weak", score=8),
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

    def __init__(self, chapters: list[dict[str, Any]] | None = None) -> None:
        self.chapters = chapters or []

    def download(self, url: str, destination_folder: str) -> DownloadResult:
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


class _FakeProposer:
    def __init__(self, segments: list[ProposedSegment] | Exception) -> None:
        self.segments = segments
        self.calls: list[dict[str, Any]] = []

    def propose(self, word_timings, audio_path, chapters, duration, *, prompt=None):
        self.calls.append(
            {
                "words": list(word_timings),
                "audio_path": audio_path,
                "audio_existed": bool(audio_path) and Path(audio_path).exists(),
                "chapters": chapters,
                "duration": duration,
                "prompt": prompt,
            }
        )
        if isinstance(self.segments, Exception):
            raise self.segments
        return list(self.segments)


class _Harness:
    """Fakes every service around the pipeline and records what reached them."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.base = tmp_path / "out" / "Talk"
        self.clips = self.base / "clips"
        self.safe_end = SOURCE_SECONDS
        self.adapter = _FakeAdapter()
        self.extract_calls: list[tuple[str, float, float]] = []
        self.transcribe_calls: list[str] = []
        self.transcribe_errors: list[BaseException] = []
        self.renders: list[dict[str, Any]] = []
        self.proposer = _FakeProposer(_segments())
        self.factory_kwargs: list[dict[str, Any]] = []

        def _subfolder(download_path, url, platform_id="video", job_id=None):
            self.clips.mkdir(parents=True, exist_ok=True)
            return str(self.base), str(self.clips)

        def _extract_audio(src, start, duration, wav_path):
            self.extract_calls.append((src, start, duration))
            Path(wav_path).parent.mkdir(parents=True, exist_ok=True)
            Path(wav_path).write_bytes(b"\x00")
            return wav_path

        def _transcribe(audio_path, **_):
            self.transcribe_calls.append(audio_path)
            if self.transcribe_errors:
                raise self.transcribe_errors.pop(0)
            return list(SOURCE_WORDS)

        def _render(video_path, output_path, start, end, *args, **kwargs):
            self.renders.append(
                {
                    "video_path": video_path,
                    "output_path": output_path,
                    "start": start,
                    "end": end,
                    "words": list(kwargs.get("word_timings") or []),
                    "discover_wav_alive": any(
                        self.clips.glob("_tmp/*/discover_source.wav")
                    ),
                }
            )
            Path(output_path).write_bytes(b"reel")
            return output_path

        def _thumb(clip_path, output_path):
            Path(output_path).write_bytes(b"thumb")
            return output_path

        def _factory(**kwargs):
            self.factory_kwargs.append(kwargs)
            return self.proposer

        monkeypatch.setattr(orch.folder_service, "create_video_subfolder", _subfolder)
        monkeypatch.setattr(orch, "resolve_adapter", lambda url: self.adapter)
        monkeypatch.setattr(
            orch.clip_service, "probe_safe_end", lambda path: self.safe_end
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

    async def run(
        self,
        store: Any,
        *,
        options: PipelineOptions | None = None,
        **payload_overrides: Any,
    ) -> _Recorder:
        await store.create(
            JobState(job_id=JOB_ID, url=URL, download_path=str(self.tmp_path))
        )
        payload: dict[str, Any] = {
            "url": URL,
            "download_path": str(self.tmp_path),
            "caption_format": "srt",
            "target_aspect_ratio": 9 / 16,
            "segment_mode": "auto",
            "language": "en-US",
            "prompt": "pricing",
            "pipeline_options": (
                options or PipelineOptions(audio_enhance=False)
            ).model_dump(),
        }
        payload.update(payload_overrides)
        bus = AsyncEventBus()
        recorder = self.recorder = _Recorder(bus)
        bus.publish = recorder  # type: ignore[method-assign]
        await orch._run_job(
            Event(type=EventType.VIDEO_REQUESTED, job_id=JOB_ID, payload=payload),
            bus,
            store,
        )
        return recorder


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    return _Harness(tmp_path, monkeypatch)


def _chapters(recorder: _Recorder) -> list[tuple[str, float, float]]:
    (detected,) = recorder.of(EventType.CHAPTERS_DETECTED)
    return [(c["title"], c["start"], c["end"]) for c in detected.payload["chapters"]]


FULL_VIDEO = [("Full Video", 0.0, SOURCE_SECONDS)]


# ── discovery on ──────────────────────────────────────────────────────────────


async def test_discover_renders_one_clip_per_kept_segment(harness, memory_store):
    recorder = await harness.run(memory_store)

    assert (await memory_store.get(JOB_ID)).status == "completed"
    assert _chapters(recorder) == [("Alpha", 10.3, 39.3), ("Beta", 50.0, 80.0)]
    assert [(r["output_path"], r["start"], r["end"]) for r in harness.renders] == [
        (str(harness.clips / "00_Alpha.mp4"), 10.3, 39.3),
        (str(harness.clips / "01_Beta.mp4"), 50.0, 80.0),
    ]
    assert all(r["video_path"] == str(harness.source) for r in harness.renders)


async def test_discover_emits_segment_events_before_chapters(harness, memory_store):
    recorder = await harness.run(memory_store)

    (proposed,) = recorder.of(EventType.SEGMENTS_PROPOSED)
    assert proposed.payload == {"count": 2, "candidates": 4}
    assert [e.payload for e in recorder.of(EventType.SEGMENT_SCORED)] == [
        {
            "index": 0,
            "start": 10.3,
            "end": 39.3,
            "score": 30,
            "breakdown": {"hook": 0.6, "value": 0.2},
        },
        {
            "index": 1,
            "start": 50.0,
            "end": 80.0,
            "score": 20,
            "breakdown": {"hook": 0.1, "value": 0.4},
        },
    ]
    types = recorder.types()
    assert (
        types.index(EventType.SEGMENTS_PROPOSED)
        < types.index(EventType.SEGMENT_SCORED)
        < types.index(EventType.CHAPTERS_DETECTED)
    )


async def test_discover_transcribes_the_source_once(harness, memory_store):
    await harness.run(memory_store)

    assert len(harness.transcribe_calls) == 1
    assert harness.extract_calls == [(str(harness.source), 0.0, SOURCE_SECONDS)]
    (call,) = harness.proposer.calls
    assert call["words"] == SOURCE_WORDS
    assert call["duration"] == SOURCE_SECONDS
    assert call["chapters"] == []
    assert call["prompt"] == "pricing"
    # The full-source wav fed the RMS features, then was deleted before the
    # chapters ran (not just with the job's temp dir at the end).
    assert call["audio_existed"] is True
    assert [r["discover_wav_alive"] for r in harness.renders] == [False, False]


async def test_discover_writes_the_words_sidecar_next_to_the_source(
    harness, memory_store
):
    await harness.run(memory_store)

    sidecar = harness.base / "source.words.json"
    assert json.loads(sidecar.read_text()) == [
        {"word": w.word, "start": w.start, "end": w.end} for w in SOURCE_WORDS
    ]


async def test_words_rebased_to_window(harness, memory_store):
    recorder = await harness.run(memory_store)

    alpha = harness.renders[0]
    duration = 39.3 - 10.3
    words = alpha["words"]
    # w10 (10.0-10.6) straddles the start; w39 (39.0-39.6) straddles the end.
    assert [w.word for w in words] == [f"w{i}" for i in range(10, 40)]
    assert all(w.start >= 0.0 for w in words), "negative caption start"
    assert all(w.end <= duration + 1e-9 for w in words), "caption past the clip end"
    assert words[0].start == 0.0 and words[0].end == pytest.approx(0.3)
    assert words[1].start == pytest.approx(0.7)  # w11 at 11.0 - 10.3
    assert words[-1].end == pytest.approx(duration)
    transcribed = recorder.of(EventType.CHAPTER_TRANSCRIBED)
    assert transcribed[0].payload["text"].split()[:2] == ["w10", "w11"]


async def test_discovered_clips_store_their_scores(harness, store):
    await harness.run(store)

    clips = sorted(await store.list_clips(job_id=JOB_ID), key=lambda c: c["start"])
    assert [
        (c["title"], c["virality_score"], c["score_breakdown"], c["summary"])
        for c in clips
    ] == [
        ("Alpha", 30, {"hook": 0.6, "value": 0.2}, "alpha summary"),
        ("Beta", 20, {"hook": 0.1, "value": 0.4}, "beta summary"),
    ]


async def test_discover_budget_follows_the_source_length(harness, memory_store):
    # A 2-minute source has a budget of one clip: only the best (Alpha).
    harness.safe_end = 120.0

    recorder = await harness.run(memory_store)

    assert _chapters(recorder) == [("Alpha", 10.3, 39.3)]


async def test_discover_uses_the_job_clip_length_range(harness, memory_store):
    await harness.run(
        memory_store,
        options=PipelineOptions(
            audio_enhance=False,
            target_length_min_seconds=15,
            target_length_max_seconds=45,
        ),
    )

    assert harness.factory_kwargs == [{"min_secs": 15, "max_secs": 45}]


async def test_discover_cleans_up_its_temp_dir(harness, memory_store):
    await harness.run(memory_store)

    assert not (harness.clips / "_tmp" / JOB_ID).exists()


# ── gate ──────────────────────────────────────────────────────────────────────


def _forbid_proposer(harness: _Harness) -> None:
    def _fail(**_: Any) -> Any:
        pytest.fail("segment proposer consulted")

    harness.monkeypatch.setattr(orch.segment_proposer, "get_segment_proposer", _fail)


async def test_discover_off_when_provider_chapter(harness, memory_store):
    harness.monkeypatch.setattr(orch.settings, "segment_provider", "chapter")
    _forbid_proposer(harness)

    recorder = await harness.run(memory_store)

    assert _chapters(recorder) == FULL_VIDEO
    # Today's path: one per-chapter transcription of the chapter's own window.
    assert harness.extract_calls == [(str(harness.source), 0.0, SOURCE_SECONDS)]
    assert len(harness.transcribe_calls) == 1
    assert EventType.SEGMENTS_PROPOSED not in recorder.types()
    assert EventType.STAGE_SKIPPED not in [
        e.type
        for e in recorder.events
        if e.payload.get("stage_id") == "segment_proposer"
    ]
    assert not (harness.base / "source.words.json").exists()


@pytest.mark.parametrize(
    ("options", "payload", "skip_event"),
    [
        (PipelineOptions(audio_enhance=False, segment_proposer=False), {}, True),
        (PipelineOptions(audio_enhance=False, transcription=False), {}, False),
        (PipelineOptions(audio_enhance=False), {"segment_mode": "chapter"}, False),
    ],
    ids=["segment_proposer-off", "transcription-off", "segment_mode-chapter"],
)
async def test_discover_off_when_option_off(
    harness, memory_store, options, payload, skip_event
):
    _forbid_proposer(harness)

    recorder = await harness.run(memory_store, options=options, **payload)

    assert _chapters(recorder) == FULL_VIDEO
    assert EventType.SEGMENTS_PROPOSED not in recorder.types()
    skipped = [
        e
        for e in recorder.of(EventType.STAGE_SKIPPED)
        if e.payload.get("stage_id") == "segment_proposer"
    ]
    assert bool(skipped) is skip_event
    assert (await memory_store.get(JOB_ID)).status == "completed"


async def test_chapters_path_never_consults_the_proposer(harness, memory_store):
    harness.adapter = _FakeAdapter(
        [
            {"title": "Intro", "start": 0.0, "end": 60.0},
            {"title": "Outro", "start": 60.0, "end": 120.0},
        ]
    )
    _forbid_proposer(harness)

    recorder = await harness.run(memory_store)

    assert _chapters(recorder) == [("Intro", 0.0, 60.0), ("Outro", 60.0, 120.0)]
    assert len(harness.transcribe_calls) == 2


async def test_short_source_keeps_full_video(harness, memory_store):
    harness.safe_end = 12.0  # below target_clip_seconds_min (20)
    harness.adapter = _FakeAdapter()

    recorder = await harness.run(memory_store)

    assert _chapters(recorder) == [("Full Video", 0.0, 12.0)]
    assert harness.proposer.calls == []
    assert len(harness.transcribe_calls) == 1


# ── fallbacks ────────────────────────────────────────────────────────────────


async def test_proposer_failure_falls_back_to_full_video(harness, memory_store):
    harness.proposer = _FakeProposer(RuntimeError("scorer exploded"))

    recorder = await harness.run(memory_store)

    assert (await memory_store.get(JOB_ID)).status == "completed"
    assert _chapters(recorder) == FULL_VIDEO
    skipped = [
        e
        for e in recorder.of(EventType.STAGE_SKIPPED)
        if e.payload.get("stage_id") == "segment_proposer"
    ]
    assert len(skipped) == 1
    # The full-source words survive the failure and are reused, not redone.
    assert len(harness.transcribe_calls) == 1
    assert harness.renders[0]["words"] == SOURCE_WORDS


async def test_unwritable_sidecar_does_not_stop_discovery(harness, memory_store):
    def _read_only(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError("read-only source folder")

    harness.monkeypatch.setattr(
        orch.segment_discovery, "write_words_sidecar", _read_only
    )

    recorder = await harness.run(memory_store)

    assert _chapters(recorder) == [("Alpha", 10.3, 39.3), ("Beta", 50.0, 80.0)]
    assert len(harness.transcribe_calls) == 1


async def test_no_surviving_segment_falls_back_to_full_video(harness, memory_store):
    harness.proposer = _FakeProposer([])

    recorder = await harness.run(memory_store)

    assert _chapters(recorder) == FULL_VIDEO
    (proposed,) = recorder.of(EventType.SEGMENTS_PROPOSED)
    assert proposed.payload == {"count": 0, "candidates": 0}
    assert (await memory_store.get(JOB_ID)).status == "completed"


async def test_transcription_failure_falls_back_to_full_video(harness, memory_store):
    harness.transcribe_errors.append(RuntimeError("whisper died"))

    recorder = await harness.run(memory_store)

    assert (await memory_store.get(JOB_ID)).status == "completed"
    assert _chapters(recorder) == FULL_VIDEO
    assert harness.proposer.calls == []
    # Discovery's attempt, then the Full Video chapter's own transcription.
    assert len(harness.transcribe_calls) == 2


async def test_cancellation_during_discovery_propagates(harness, memory_store):
    calls = 0

    async def _cancel_first(*args: Any, **kwargs: Any) -> Any:
        # Only discovery's call is cancelled: a swallowed cancel would go on
        # to the Full Video chapter, whose transcription succeeds.
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError
        return list(SOURCE_WORDS)

    harness.monkeypatch.setattr(
        orch.transcription_service, "transcribe_words_async", _cancel_first
    )

    with pytest.raises(asyncio.CancelledError):
        await harness.run(memory_store)

    assert calls == 1
    assert harness.recorder.of(EventType.CHAPTERS_DETECTED) == []
    assert harness.recorder.of(EventType.STAGE_SKIPPED) == []
    job = await memory_store.get(JOB_ID)
    assert job.status == "running"
    assert not (harness.clips / "_tmp" / JOB_ID).exists()


# ── the real heuristic proposer ───────────────────────────────────────────────


_SENTENCES = [
    "How do you save money every single month?",
    "Here are 3 steps that actually work for most people.",
    "First, track every expense for 30 days and write it down.",
    "um so yeah anyway we were just talking about the weather there.",
    "it was fine and then it was kind of okay I guess.",
    "Why do 9 out of 10 budgets fail in the first week?",
    "Because you never set a target, so you never know when you win.",
]


def _speech(seconds: float) -> list[WordTiming]:
    words: list[WordTiming] = []
    t = 0.0
    i = 0
    while t < seconds - 1.0:
        # Each sentence gets its own words (a suffix per sentence), so windows
        # do not repeat each other; the hook word and punctuation stay.
        first, *rest = _SENTENCES[i % len(_SENTENCES)].split()
        tokens = [first] + [
            tok.rstrip(".?,") + f"s{i}" + tok[len(tok.rstrip(".?,")):] for tok in rest
        ]
        for token in tokens:
            words.append(WordTiming(token, t, t + 0.35))
            t += 0.45
        t += 0.7  # sentence pause
        i += 1
    return words


async def test_real_heuristic_proposer_yields_several_scored_clips(
    harness, memory_store
):
    # The real factory: settings.segment_provider is "local_heuristic" here.
    harness.monkeypatch.setattr(
        orch.segment_proposer, "get_segment_proposer", real_get_segment_proposer
    )
    speech = _speech(180.0)
    harness.safe_end = 180.0
    harness.monkeypatch.setattr(
        orch.transcription_service,
        "transcribe_to_words",
        lambda audio_path, **_: list(speech),
    )

    await harness.run(memory_store)

    clips = sorted(
        await memory_store.list_clips(job_id=JOB_ID), key=lambda c: c["start"]
    )
    assert len(clips) > 1
    assert all(isinstance(c["virality_score"], int) for c in clips)
    assert all(c["score_breakdown"] for c in clips)
    for a, b in zip(clips, clips[1:]):
        assert a["end"] <= b["start"]
    assert all(20.0 <= c["end"] - c["start"] <= 60.0 for c in clips)



# ── near-duplicate clips ──────────────────────────────────────────────────────

_DIALOGUE = (
    "Anita? You in? Yeah. Wow, I didn't know you were in town! "
    "I got back last year. Why didn't you call me? Sorry, I meant to get in touch."
)


async def test_two_windows_repeating_the_same_lines_yield_one_clip(
    harness, memory_store
):
    harness.proposer = _FakeProposer(
        [
            ProposedSegment(start=10.0, end=40.0, title="Scene", score=30, text=_DIALOGUE),
            ProposedSegment(start=200.0, end=230.0, title="Scene again", score=29, text=_DIALOGUE),
            ProposedSegment(
                start=100.0, end=130.0, title="Thesis", score=22,
                text="Emptiness is an unnatural but common state for a theatre.",
            ),
        ]
    )

    recorder = await harness.run(memory_store)

    assert _chapters(recorder) == [("Scene", 10.0, 40.0), ("Thesis", 100.0, 130.0)]


def _dialogue_talk(seconds: float) -> list[WordTiming]:
    """Unique narration, with the same dialogue played at 60 s and at 240 s."""
    words: list[WordTiming] = []
    t = 0.0
    n = 0
    while t < seconds - 1.0:
        if 60.0 <= t < 100.0 or 240.0 <= t < 280.0:
            tokens = _DIALOGUE.split()
        else:
            tokens = [f"narration{n}x{k}" for k in range(9)]
            tokens[-1] += "."
            n += 1
        for token in tokens:
            words.append(WordTiming(token, t, t + 0.35))
            t += 0.45
        t += 0.7
    return words


async def test_real_proposer_keeps_one_of_two_repeated_dialogue_scenes(
    harness, memory_store
):
    harness.monkeypatch.setattr(
        orch.segment_proposer, "get_segment_proposer", real_get_segment_proposer
    )
    speech = _dialogue_talk(SOURCE_SECONDS)
    harness.monkeypatch.setattr(
        orch.transcription_service,
        "transcribe_to_words",
        lambda audio_path, **_: list(speech),
    )

    await harness.run(memory_store)

    clips = await memory_store.list_clips(job_id=JOB_ID)
    scenes = [
        c for c in clips if "Anita?" in c["transcript"] or "town!" in c["transcript"]
    ]
    assert len(clips) >= 2
    assert len(scenes) == 1, [(c["start"], c["end"]) for c in clips]


# ── optional local-LLM re-rank (T040) ─────────────────────────────────────────


class _FakeOllama:
    """Stands in for ``segment_rerank._ask_ollama``: no network."""

    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def __call__(self, prompt_text: str, ids: list[str], *, transport: Any = None) -> str:
        self.prompts.append(prompt_text)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _use_ollama(harness: _Harness, reply: str | Exception, *, provider: str = "ollama") -> _FakeOllama:
    fake = _FakeOllama(reply)
    harness.monkeypatch.setattr(segment_rerank, "_ask_ollama", fake)
    harness.monkeypatch.setattr(orch.settings, "ollama_enabled", True)
    harness.monkeypatch.setattr(orch.settings, "segment_rerank_provider", provider)
    # Ollama is on for the re-rank; keep the per-clip social step offline.
    harness.monkeypatch.setattr(
        orch.ollama_service, "generate_social_content", lambda *args, **kwargs: ("", [])
    )
    return fake


def _candidate_blocks(prompt: str) -> list[tuple[str, str]]:
    return re.findall(r'<candidate id="(c\d+)">(.*?)</candidate>', prompt)


async def test_discover_stores_the_blended_score(harness, store):
    fake = _use_ollama(harness, '{"c1": 40, "c2": 60}')

    recorder = await harness.run(store)

    # The shortlist is Alpha and Beta, in start order ("Overlap" overlaps
    # Alpha, "Weak" is under the bar); the job's prompt rides along as data.
    (prompt,) = fake.prompts
    assert _candidate_blocks(prompt) == [("c1", "alpha summary"), ("c2", "beta summary")]
    assert "<viewer_request>pricing</viewer_request>" in prompt
    # 0.5 * heuristic + 0.5 * model: Alpha 0.5*30 + 0.5*40 = 35, Beta 40.
    clips = sorted(await store.list_clips(job_id=JOB_ID), key=lambda c: c["start"])
    assert [(c["title"], c["virality_score"], c["score_breakdown"], c["summary"]) for c in clips] == [
        ("Alpha", 35, {"hook": 0.6, "value": 0.2}, "alpha summary"),
        ("Beta", 40, {"hook": 0.1, "value": 0.4}, "beta summary"),
    ]
    assert [e.payload["score"] for e in recorder.of(EventType.SEGMENT_SCORED)] == [35, 40]
    (proposed,) = recorder.of(EventType.SEGMENTS_PROPOSED)
    assert proposed.payload == {"count": 2, "candidates": 4}


async def test_discover_selection_runs_on_the_blended_scores(harness, memory_store):
    # Alpha 0.5*30 + 0 = 15, Beta 0.5*20 + 0.5*100 = 60: Alpha is now under
    # the relative bar (ceil(0.6 * 60) = 36) and only Beta is kept.
    _use_ollama(harness, '{"c1": 0, "c2": 100}')

    recorder = await harness.run(memory_store)

    assert _chapters(recorder) == [("Beta", 50.0, 80.0)]
    assert [c["virality_score"] for c in await memory_store.list_clips(job_id=JOB_ID)] == [60]


async def test_discover_rerank_off_never_asks_the_model(harness, memory_store):
    fake = _use_ollama(harness, '{"c1": 0, "c2": 100}', provider="none")

    recorder = await harness.run(memory_store)

    assert fake.prompts == []
    assert _chapters(recorder) == [("Alpha", 10.3, 39.3), ("Beta", 50.0, 80.0)]
    clips = sorted(await memory_store.list_clips(job_id=JOB_ID), key=lambda c: c["start"])
    assert [c["virality_score"] for c in clips] == [30, 20]


async def test_discover_rerank_failure_keeps_the_heuristic_clips(harness, memory_store):
    fake = _use_ollama(harness, httpx.ConnectError("connection refused"))

    recorder = await harness.run(memory_store)

    assert len(fake.prompts) == 1
    assert (await memory_store.get(JOB_ID)).status == "completed"
    assert _chapters(recorder) == [("Alpha", 10.3, 39.3), ("Beta", 50.0, 80.0)]
    clips = sorted(await memory_store.list_clips(job_id=JOB_ID), key=lambda c: c["start"])
    assert [c["virality_score"] for c in clips] == [30, 20]
    # A failed re-rank is not a failed discovery.
    skipped = [
        e for e in recorder.of(EventType.STAGE_SKIPPED) if e.payload.get("stage_id") == "segment_proposer"
    ]
    assert skipped == []


# ── single-clip re-render reuses the sidecar ──────────────────────────────────

CLIP_ID = "clip-0000-discover"


async def _seed_rendered_clip(store: Any, harness: _Harness, *, title: str) -> Path:
    harness.clips.mkdir(parents=True, exist_ok=True)
    harness.source.write_bytes(b"source")
    output = harness.clips / "01_Beta.mp4"
    output.write_bytes(b"old")
    thumb = harness.clips / "01_Beta_thumb.jpg"
    thumb.write_bytes(b"old-thumb")
    await store.create(JobState(job_id=JOB_ID, url=URL, download_path="/downloads"))

    def _complete(s: JobState) -> None:
        s.status = "completed"
        s.video_path = str(harness.source)
        s.output_paths = [str(output)]

    await store.update(JOB_ID, _complete)
    await store.upsert_clip(
        JOB_ID,
        CLIP_ID,
        lambda c: c.update(
            {
                "start": 50.0,
                "end": 80.0,
                "output_path": str(output),
                "thumbnail_path": str(thumb),
                "title": title,
                "virality_score": 20,
            }
        ),
    )
    return output


async def _rerender(store: Any) -> _Recorder:
    bus = AsyncEventBus()
    recorder = _Recorder(bus)
    bus.publish = recorder  # type: ignore[method-assign]
    payload = {
        "rerender_clip_id": CLIP_ID,
        "url": URL,
        "download_path": "/downloads",
        "pipeline_options": PipelineOptions(audio_enhance=False).model_dump(),
    }
    await orch._run_job(
        Event(type=EventType.VIDEO_REQUESTED, job_id=JOB_ID, payload=payload),
        bus,
        store,
    )
    return recorder


async def test_rerender_reuses_the_words_sidecar(harness, store):
    await _seed_rendered_clip(store, harness, title="Beta")
    orch.segment_discovery.write_words_sidecar(str(harness.source), SOURCE_WORDS)

    await _rerender(store)

    assert harness.transcribe_calls == []
    assert harness.extract_calls == []
    (render,) = harness.renders
    assert [(w.word, w.start) for w in render["words"]] == [
        (f"w{i}", float(i - 50)) for i in range(50, 80)
    ]
    clip = await store.get_clip(CLIP_ID)
    assert clip["transcript"].split()[:2] == ["w50", "w51"]
    assert clip["virality_score"] == 20


async def test_rerender_without_sidecar_transcribes_the_window(harness, store):
    await _seed_rendered_clip(store, harness, title="Beta")

    await _rerender(store)

    assert harness.extract_calls == [(str(harness.source), 50.0, 30.0)]
    assert len(harness.transcribe_calls) == 1


async def test_rerender_overwrites_the_clip_file_after_a_title_edit(harness, store):
    output = await _seed_rendered_clip(store, harness, title="Renamed by the user")

    await _rerender(store)

    (render,) = harness.renders
    assert render["output_path"] == str(output)
    assert output.read_bytes() == b"reel"
    clip = await store.get_clip(CLIP_ID)
    assert clip["output_path"] == str(output)
    assert clip["thumbnail_path"] == str(harness.clips / "01_Beta_thumb.jpg")
    assert sorted(p.name for p in harness.clips.iterdir() if p.is_file()) == [
        "01_Beta.mp4",
        "01_Beta_thumb.jpg",
    ]
