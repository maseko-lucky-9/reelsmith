"""Per-clip words sidecar (spec 016, T126).

``_process_chapter`` writes the words of each rendered reel, on the clip's
clock (0 = the reel's first frame) and clipped to the clip window, to
``<job folder>/.clip_words/<clip stem>.words.json``: beside the clips folder,
never in it. Every render rewrites it (a render without transcription
removes it). ``clip_words.clip_words_path`` refuses unsafe paths; a job
delete removes the folder with the job folder; an export never copies it.
``clip_words.default_timeline`` builds the editor's starting timeline.
Offline: render and transcription are fakes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import InMemoryJobStore
from app.domain.models import JobState, PipelineOptions
from app.services import clip_words, export_service, retention
from app.services.timeline_render_service import build_render_plan
from app.services.transcription_service import WordTiming
from app.workers import orchestrator as orch

JOB_ID = "job-clip-words"
# Chapter 100-130 s of the source.
CHAPTER = {"index": 0, "title": "Talk", "start": 100.0, "end": 130.0}
SOURCE_WORDS = [
    WordTiming("before", 98.0, 99.5),  # wholly before the window: dropped
    WordTiming("we", 104.0, 104.2),
    WordTiming("saw", 104.5, 104.9),
    WordTiming("rivers", 115.0, 115.5),
    WordTiming("late", 129.8, 130.6),  # straddles the end: clamped to 30.0
    WordTiming("after", 131.0, 131.5),  # wholly after: dropped
]
EXPECTED = [
    {"word": "we", "start": 4.0, "end": 4.2},
    {"word": "saw", "start": 4.5, "end": 4.9},
    {"word": "rivers", "start": 15.0, "end": 15.5},
    {"word": "late", "start": 29.8, "end": 30.0},
]
_OPTS = PipelineOptions(audio_enhance=False, thumbnail=False, broll=False, reframe=False)


@pytest.fixture
def render(monkeypatch) -> list[str]:
    outputs: list[str] = []

    def _render(video_path, output_path, start, end, *args, **kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"reel")
        outputs.append(output_path)
        return output_path

    monkeypatch.setattr(orch.render_service, "render_clip", _render)
    return outputs


async def _chapter(
    tmp_path: Path, *, opts=_OPTS, source_words=SOURCE_WORDS, render_to: str | None = None
) -> str | None:
    store = InMemoryJobStore()
    await store.create(JobState(job_id=JOB_ID, url="https://x/v", download_path=str(tmp_path)))
    cleanup = tmp_path / "tmp"
    cleanup.mkdir(exist_ok=True)
    return await orch._process_chapter(
        chapter=dict(CHAPTER),
        job_id=JOB_ID,
        video_path=str(tmp_path / "source.mp4"),
        clips_folder=str(tmp_path / "clips"),
        cleanup_root=cleanup,
        caption_format="srt",
        target_aspect_ratio=9 / 16,
        bus=AsyncEventBus(),
        store=store,
        pipeline_options=opts,
        source_words=source_words,
        render_to=render_to,
    )


def _approx(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**w, "start": pytest.approx(w["start"]), "end": pytest.approx(w["end"])} for w in words]


# ── Written by the pipeline ───────────────────────────────────────────────────


async def test_reused_source_words_are_written_on_the_clip_clock(tmp_path, render):
    output = await _chapter(tmp_path)

    assert output == str(tmp_path / "clips" / "00_Talk.mp4")
    sidecar = tmp_path / ".clip_words" / "00_Talk.words.json"
    assert clip_words.clip_words_path(output) == sidecar
    assert json.loads(sidecar.read_text()) == _approx(EXPECTED)
    # The clips folder holds the reel only.
    assert sorted(p.name for p in (tmp_path / "clips").iterdir()) == ["00_Talk.mp4"]


async def test_transcribed_words_are_written_clipped_to_the_window(tmp_path, render, monkeypatch):
    def _extract(src, start, duration, wav_path):
        Path(wav_path).write_bytes(b"wav")
        return wav_path

    async def _transcribe(audio_path, *, language, audio_duration_s, repeatable=False):
        assert audio_duration_s == pytest.approx(30.0)
        # The chapter wav starts at the window: times are already clip-relative.
        return [WordTiming("hello", 1.0, 1.4), WordTiming("tail", 29.9, 30.4)]

    monkeypatch.setattr(orch.clip_service, "extract_audio", _extract)
    monkeypatch.setattr(orch.transcription_service, "transcribe_words_async", _transcribe)

    output = await _chapter(tmp_path, source_words=None)

    assert clip_words.read_clip_words(output) == [
        WordTiming("hello", 1.0, 1.4),
        WordTiming("tail", pytest.approx(29.9), 30.0),
    ]


async def test_a_rerender_overwrites_stale_words(tmp_path, render):
    reel = tmp_path / "clips" / "00_Renamed.mp4"
    reel.parent.mkdir()
    reel.write_bytes(b"old reel")
    clip_words.write_clip_words(str(reel), [WordTiming("stale", 0.0, 1.0)])

    output = await _chapter(tmp_path, render_to=str(reel))

    assert output == str(reel)
    assert json.loads(clip_words.clip_words_path(output).read_text()) == _approx(EXPECTED)


async def test_a_render_without_transcription_removes_stale_words(tmp_path, render):
    """No words were made: none are cached, so the editor backfills."""
    reel = tmp_path / "clips" / "00_Talk.mp4"
    reel.parent.mkdir()
    reel.write_bytes(b"old reel")
    clip_words.write_clip_words(str(reel), [WordTiming("stale", 0.0, 1.0)])

    output = await _chapter(tmp_path, opts=_OPTS.model_copy(update={"transcription": False}), render_to=str(reel))

    assert output is not None and Path(output).is_file()
    assert not clip_words.clip_words_path(output).exists()


async def test_a_failed_sidecar_write_does_not_fail_the_chapter(tmp_path, render, monkeypatch):
    def _fail(clip_path, words):
        raise PermissionError("read-only folder")

    monkeypatch.setattr(orch.clip_words, "write_clip_words", _fail)

    output = await _chapter(tmp_path)

    assert output is not None and Path(output).is_file()
    assert not clip_words.clip_words_path(output).exists()


# ── The sidecar's path ────────────────────────────────────────────────────────


def test_the_path_is_beside_the_clips_folder(tmp_path):
    clip = tmp_path / "Talk-1234" / "clips" / "03_A.B c.mp4"

    assert clip_words.clip_words_path(str(clip)) == (tmp_path / "Talk-1234" / ".clip_words" / "03_A.B c.words.json")


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "clips/00_Talk.mp4",
        "/jobs/x/clips/../../etc/00_Talk.mp4",
        "/jobs/x/clips/00\x00.mp4",
        "/jobs/x/clips/..",
        "/",
    ],
)
def test_unsafe_clip_paths_are_refused(raw):
    assert clip_words.clip_words_path(raw) is None
    assert clip_words.read_clip_words(raw) is None


def test_a_symlinked_words_folder_is_refused_and_nothing_written(tmp_path):
    job = tmp_path / "job"
    (job / "clips").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, job / ".clip_words")
    clip = str(job / "clips" / "00_Talk.mp4")

    assert clip_words.clip_words_path(clip) is None
    assert clip_words.write_clip_words(clip, [WordTiming("x", 0.0, 1.0)]) is None
    assert list(elsewhere.iterdir()) == []


def test_a_symlinked_sidecar_is_refused(tmp_path):
    job = tmp_path / "job"
    (job / ".clip_words").mkdir(parents=True)
    target = tmp_path / "secret.json"
    target.write_text(json.dumps([{"word": "secret", "start": 0, "end": 1}]))
    os.symlink(target, job / ".clip_words" / "00_Talk.words.json")
    clip = str(job / "clips" / "00_Talk.mp4")

    assert clip_words.clip_words_path(clip) is None
    assert clip_words.read_clip_words(clip) is None


# ── Removed with the job; never exported ──────────────────────────────────────


async def test_a_job_delete_removes_the_words_folder(tmp_path, monkeypatch):
    root = (tmp_path / "downloads").resolve()
    (root / "uploads").mkdir(parents=True)
    monkeypatch.setattr(retention.settings, "default_download_path", str(root))
    monkeypatch.setattr(retention.settings, "export_base_folder", str(tmp_path / "share"))
    folder = root / f"Some_Talk-{JOB_ID[:8]}"
    reel = folder / "clips" / "00_Talk.mp4"
    reel.parent.mkdir(parents=True)
    reel.write_bytes(b"reel")
    sidecar = clip_words.write_clip_words(str(reel), [WordTiming("x", 0.0, 1.0)])
    store = InMemoryJobStore()
    job = await store.create(
        JobState(
            job_id=JOB_ID,
            url="https://www.youtube.com/watch?v=x",
            download_path=str(root),
            destination_folder=str(folder),
            clips_folder=str(folder / "clips"),
            output_paths=[str(reel)],
        )
    )

    deleted = await retention.delete_job_files(job, store)

    assert str(sidecar) in deleted
    assert not (folder / ".clip_words").exists()
    assert not folder.exists()


def test_an_export_copies_the_reel_only(tmp_path):
    reel = tmp_path / "job" / "clips" / "00_Talk.mp4"
    reel.parent.mkdir(parents=True)
    reel.write_bytes(b"reel")
    clip_words.write_clip_words(str(reel), [WordTiming("x", 0.0, 1.0)])
    export_dir = tmp_path / "share" / JOB_ID

    export_service.export_clips([str(reel)], str(export_dir))

    assert sorted(p.name for p in export_dir.iterdir()) == ["00_Talk.mp4"]


# ── Default timeline ──────────────────────────────────────────────────────────


def test_default_timeline_has_per_word_caption_items():
    words = [WordTiming(w["word"], w["start"], w["end"]) for w in EXPECTED]

    timeline = clip_words.default_timeline(words, 30.0, words_per_caption=3)

    kinds = [t["kind"] for t in timeline["tracks"]]
    assert kinds == ["video", "caption", "text-overlay"]
    video, caption, overlay = timeline["tracks"]
    assert video["items"] == [{"start": 0.0, "end": 30.0, "src": "main", "trim_start": 0.0}]
    assert overlay["items"] == []
    assert [c["text"] for c in caption["items"]] == ["we saw rivers", "late"]
    first, second = caption["items"]
    assert (first["start"], first["end"]) == (pytest.approx(4.0), pytest.approx(15.5))
    assert first["words"] == _approx(EXPECTED[:3])
    assert second["words"] == _approx(EXPECTED[3:])
    assert first["style"] == "default"
    # The v1 render plan accepts it unchanged.
    plan = build_render_plan(timeline, "/clips/00_Talk.mp4")
    assert len(plan.captions) == 2 and plan.duration == pytest.approx(30.0)


def test_default_timeline_zero_length_word_still_gets_a_valid_caption():
    timeline = clip_words.default_timeline([WordTiming("um", 5.0, 5.0)], 10.0, words_per_caption=3)

    (item,) = timeline["tracks"][1]["items"]
    assert item["end"] > item["start"] == 5.0
    build_render_plan(timeline, "/clips/x.mp4")


def test_default_timeline_without_words_is_the_bare_reel():
    timeline = clip_words.default_timeline([], 12.0, words_per_caption=3)

    assert timeline["tracks"][0]["items"][0]["end"] == 12.0
    assert timeline["tracks"][1]["items"] == []
