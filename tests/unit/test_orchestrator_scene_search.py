"""AI scene search in the pipeline (spec 014): a prompt picks the clips.

The sidecar is ``tests/fixtures/fake_scene_index.py`` (same argv and files as
``scripts/scene_index.py``), so these runs are offline. The source is the
discovery harness's 360 s talk (one word a second, so 15 s sentences); chunk 5
(40-48 s) is what the prompt describes.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.domain.events import Event, EventType
from app.domain.models import JobState
from app.services import scene_search
from app.workers import orchestrator as orch
from tests.unit import test_orchestrator_discover as discover
from tests.unit import test_orchestrator_reprompt as reprompt

FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_scene_index.py"
PROMPT = "a red car driving past"
YOUTUBE_CHAPTERS = [
    {"title": "Intro", "start": 0.0, "end": 120.0},
    {"title": "Main", "start": 120.0, "end": 360.0},
]


# Both job stores (memory and SQLite), from the discovery tests.
store = discover.store


def _chunks(match: int = 5, total: int = 45) -> list[list[float]]:
    return [[i * 8.0, (i + 1) * 8.0, 0.5 if i == match else 0.0] for i in range(total)]


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    """Point scene search at the fake sidecar; returns a function that sets its spec."""
    spec = tmp_path / "scene-spec.json"

    def _write(**data: Any) -> Path:
        spec.write_text(json.dumps({"prompt": PROMPT, "chunks": _chunks(), **data}))
        return spec

    _write()
    monkeypatch.setenv("FAKE_SCENE_SPEC", str(spec))
    monkeypatch.setattr(orch.settings, "scene_search_provider", "mlx")
    monkeypatch.setattr(orch.settings, "scene_python", sys.executable)
    monkeypatch.setattr(scene_search, "SCENE_SCRIPT", FAKE)
    return _write


def _calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "scene-spec.json.calls"
    return path.read_text().split() if path.exists() else []


@pytest.fixture
def harness(tmp_path, monkeypatch) -> discover._Harness:
    h = discover._Harness(tmp_path, monkeypatch)
    h.adapter = discover._FakeAdapter(chapters=YOUTUBE_CHAPTERS)
    return h


def _skipped(recorder) -> list[str]:
    return [e.payload.get("reason", "") for e in recorder.of(EventType.STAGE_SKIPPED)
            if e.payload.get("stage_id") == "scene_search"]  # fmt: skip


# ── run ───────────────────────────────────────────────────────────────────────


async def test_a_prompt_overrides_the_youtube_chapters(harness, sidecar, store):
    discover._forbid_proposer(harness)

    recorder = await harness.run(store, prompt=PROMPT, segment_mode="chapter")

    assert (await store.get(discover.JOB_ID)).status == "completed"
    # 40-48 s grown to 20 s (34-54) and snapped out to its sentences.
    assert discover._chapters(recorder) == [("Clip 1", 30.0, 59.6)]
    assert [(r["start"], r["end"]) for r in harness.renders] == [(30.0, 59.6)]
    types = recorder.types()
    assert types.index(EventType.SCENE_INDEX_PROGRESS) < types.index(EventType.SCENES_INDEXED)
    assert types.index(EventType.SCENES_INDEXED) < types.index(EventType.CHAPTERS_DETECTED)
    (indexed,) = recorder.of(EventType.SCENES_INDEXED)
    assert indexed.payload["chunks"] == 45 and indexed.payload["reused"] is False
    progress = [e.payload for e in recorder.of(EventType.SCENE_INDEX_PROGRESS)]
    assert progress[0] == {"done": 0, "total": 45} and progress[-1] == {"done": 45, "total": 45}
    assert (harness.base / "scenes" / "embeddings.npy").is_file()
    # The whole source was transcribed once, beside the indexing, and the
    # clip reuses those words.
    assert len(harness.transcribe_calls) == 1
    assert (harness.base / "source.words.json").is_file()


async def test_without_scene_search_the_youtube_chapters_stay(harness, sidecar, monkeypatch):
    monkeypatch.setattr(orch.settings, "scene_search_provider", "off")
    recorder = await harness.run(discover.InMemoryJobStore(), prompt=PROMPT)
    assert [c[0] for c in discover._chapters(recorder)] == ["Intro", "Main"]
    assert _calls(harness.tmp_path) == []


async def test_the_judges_title_reason_and_emotion_reach_the_clip(harness, sidecar, store):
    async def _judge(candidates, *, prompt, video_path, job_id="-"):
        assert prompt == PROMPT and video_path == str(harness.source)
        return [replace(candidates[0], title="Red car at dusk", reason="a red car passes the camera",
                        emotion="joy", summary="a red car passes the camera", score=88)]  # fmt: skip

    harness.monkeypatch.setattr(orch.segment_rerank, "judge_scenes", _judge)

    await harness.run(store, prompt=PROMPT)

    (clip,) = await store.list_clips(job_id=discover.JOB_ID)
    assert clip["title"] == "Red car at dusk"
    assert clip["summary"] == "a red car passes the camera"
    assert clip["virality_score"] == 88
    assert clip["score_breakdown"]["emotion"] == "joy"
    assert clip["score_breakdown"]["reason"] == "a red car passes the camera"
    assert clip["output_path"].endswith("00_Red car at dusk.mp4")


async def test_a_failed_index_falls_back_to_the_chapters(harness, sidecar):
    sidecar(fail="index")
    recorder = await harness.run(discover.InMemoryJobStore(), prompt=PROMPT)

    assert [c[0] for c in discover._chapters(recorder)] == ["Intro", "Main"]
    assert any("scene indexing failed" in r for r in _skipped(recorder))
    assert EventType.SCENES_INDEXED not in recorder.types()


async def test_an_unexpected_error_in_scene_search_never_fails_the_job(harness, sidecar):
    """Review finding: only cancellation may leave the scene stage."""

    def _boom(*args: Any, **kwargs: Any):
        raise ValueError("unexpected")

    harness.monkeypatch.setattr(orch.scene_search, "search", _boom)
    store = discover.InMemoryJobStore()
    recorder = await harness.run(store, prompt=PROMPT)

    assert (await store.get(discover.JOB_ID)).status == "completed"
    assert [c[0] for c in discover._chapters(recorder)] == ["Intro", "Main"]
    assert "scene search failed: unexpected" in _skipped(recorder)


async def test_no_matching_moment_falls_back_to_the_chapters(harness, sidecar):
    async def _reject_all(candidates, **_: Any):
        return []

    harness.monkeypatch.setattr(orch.segment_rerank, "judge_scenes", _reject_all)
    recorder = await harness.run(discover.InMemoryJobStore(), prompt=PROMPT)

    assert [c[0] for c in discover._chapters(recorder)] == ["Intro", "Main"]
    assert "no moment matched the prompt" in _skipped(recorder)


async def test_a_failed_index_hands_its_words_to_discovery(tmp_path, monkeypatch, sidecar):
    """No chapters, scene search fails: discovery runs on the words already transcribed."""
    h = discover._Harness(tmp_path, monkeypatch)
    sidecar(fail="index")
    recorder = await h.run(discover.InMemoryJobStore(), prompt=PROMPT)

    assert discover._chapters(recorder) == [("Alpha", 10.3, 39.3), ("Beta", 50.0, 80.0)]
    assert len(h.transcribe_calls) == 1
    assert h.proposer.calls[0]["prompt"] == PROMPT


async def test_cancel_during_indexing_stops_the_sidecar_and_ends_cancelled(harness, sidecar, tmp_path):
    spec = sidecar(hang_after=1)
    store = discover.InMemoryJobStore()
    await store.create(JobState(job_id=discover.JOB_ID, url=discover.URL, download_path=str(tmp_path)))
    recorder = discover._Recorder(orch.AsyncEventBus())
    bus = orch.AsyncEventBus()
    bus.publish = recorder  # type: ignore[method-assign]
    payload = {"url": discover.URL, "download_path": str(tmp_path), "prompt": PROMPT,
               "pipeline_options": discover.PipelineOptions(audio_enhance=False).model_dump()}  # fmt: skip
    task = asyncio.create_task(
        orch._run_job(Event(type=EventType.VIDEO_REQUESTED, job_id=discover.JOB_ID, payload=payload), bus, store)
    )
    orch._track_job_task(discover.JOB_ID, task)
    for _ in range(500):
        if recorder.of(EventType.SCENE_INDEX_PROGRESS) and Path(f"{spec}.pid").exists():
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("the sidecar never reported progress")

    assert orch.cancel_job(discover.JOB_ID) is True
    with pytest.raises(asyncio.CancelledError):
        await task

    assert (await store.get(discover.JOB_ID)).status == "cancelled"
    assert recorder.types()[-1] is EventType.JOB_CANCELLED
    assert Path(f"{spec}.sigterm").read_text() == "1"  # asked to clean up first
    pid = int(Path(f"{spec}.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not (harness.base / "scenes").exists()
    assert [p.name for p in harness.base.iterdir() if p.name.startswith(".scenes")] == []


# ── reprompt ──────────────────────────────────────────────────────────────────


@pytest.fixture
def reprompt_harness(tmp_path, monkeypatch) -> reprompt._Harness:
    orch.release_reprompt(reprompt.JOB_ID)
    return reprompt._Harness(tmp_path, monkeypatch)


async def test_reprompt_reuses_the_scene_index_and_rebuilds_it_when_the_source_changes(
    reprompt_harness, sidecar, tmp_path
):
    h = reprompt_harness
    store = reprompt.InMemoryJobStore()
    await h.complete_job(store)
    sidecar(prompt=reprompt.PROMPT)

    first = await h.reprompt(store)
    assert first.types()[-1] is EventType.JOB_COMPLETED
    assert _calls(tmp_path) == ["index", "embed-texts"]
    (indexed,) = first.of(EventType.SCENES_INDEXED)
    assert indexed.payload["reused"] is False
    live = await reprompt._live(store)
    assert [(c["start"], c["end"]) for c in live] == [(30.0, 59.6)]
    assert reprompt._names(live) == ["02_Clip 1.mp4"]  # indexes follow the old clips

    second = await h.reprompt(store)
    (indexed,) = second.of(EventType.SCENES_INDEXED)
    assert indexed.payload["reused"] is True
    assert EventType.SCENE_INDEX_PROGRESS not in second.types()
    # Only the prompt was embedded again; the sentences came from the cache.
    assert _calls(tmp_path) == ["index", "embed-texts", "embed-texts"]

    h.source.write_bytes(b"a re-downloaded source")
    third = await h.reprompt(store)
    (indexed,) = third.of(EventType.SCENES_INDEXED)
    assert indexed.payload["reused"] is False
    assert _calls(tmp_path).count("index") == 2


async def test_reprompt_with_scene_search_needs_no_discovery_provider(reprompt_harness, sidecar, monkeypatch):
    monkeypatch.setattr(orch.settings, "segment_provider", "chapter")
    assert orch.reprompt_unavailable_reason(PROMPT) is None
    assert orch.reprompt_unavailable_reason(None) is not None
    monkeypatch.setattr(orch.settings, "scene_search_provider", "off")
    assert orch.reprompt_unavailable_reason(PROMPT) is not None


async def test_reprompt_with_no_matching_moment_fails_and_keeps_the_clips(reprompt_harness, sidecar, monkeypatch):
    h = reprompt_harness
    store = reprompt.InMemoryJobStore()
    old = await h.complete_job(store)
    sidecar(prompt=reprompt.PROMPT)

    async def _reject_all(candidates, **_: Any):
        return []

    monkeypatch.setattr(orch.segment_rerank, "judge_scenes", _reject_all)
    recorder = await h.reprompt(store)

    (failed,) = recorder.of(EventType.REPROMPT_FAILED)
    assert "no moment of the source matched the prompt" in failed.payload["error"]
    assert reprompt._names(await reprompt._live(store)) == reprompt._names(old)
