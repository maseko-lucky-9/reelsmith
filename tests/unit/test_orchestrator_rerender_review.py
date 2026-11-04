"""Re-render review fixes (spec 016 Phase 4a).

* A refused or failed clip re-render emits ``ClipRerenderFailed`` (the UI
  is not left on "queued").
* An edited reel's real length is recorded (``clip_words.read_reel_duration``)
  for the manifest and the default timeline; a plain re-render forgets it.
* A re-render of an edit never reads the source words sidecar: its captions
  come only from the edit.
* More unplannable edits are refused before anything is touched.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.bus.event_bus import AsyncEventBus
from app.domain.events import EventType
from app.services import manifest_service, segment_discovery
from app.services.transcription_service import WordTiming
from app.workers import orchestrator as orch

# Shared fixtures and helpers of the re-render suite.
from tests.unit.test_orchestrator_rerender import (  # noqa: F401
    CLIP_ID,
    _Recorder,
    _save_edit,
    _seed,
    _trigger,
    _v2_edit,
    layout,
    plan_renders,
    render_calls,
    store,
)


def _recording_bus() -> tuple[AsyncEventBus, _Recorder]:
    bus = AsyncEventBus()
    recorder = _Recorder(bus)
    bus.publish = recorder  # type: ignore[method-assign]
    return bus, recorder


def _failures(recorder: _Recorder) -> list[dict[str, Any]]:
    return [e.payload for e in recorder.events if e.type == EventType.CLIP_RERENDER_FAILED]


@pytest.mark.parametrize(
    "timeline",
    [
        # The web editor's zero-length placeholder video item (v1).
        {"tracks": [{"kind": "video", "items": [{"start": 0, "end": 0, "src": "main", "trim_start": 0}]}]},
        # A v1 edit with no video track at all.
        {"tracks": [{"kind": "caption", "items": [{"start": 0, "end": 1, "text": "hi"}]}]},
        # A v2 edit naming an unknown caption style.
        {
            "schema_version": 2,
            "caption_style": "no-such-style",
            "tracks": [{"kind": "video", "items": [{"start": 6.0, "end": 8.0}]}],
        },
        # A v1 edit whose video items leave a gap (captions would drift).
        {
            "tracks": [
                {
                    "kind": "video",
                    "items": [
                        {"start": 0, "end": 1, "src": "main", "trim_start": 0},
                        {"start": 2, "end": 3, "src": "main", "trim_start": 2},
                    ],
                }
            ]
        },
    ],
    ids=["v1-zero-length-placeholder", "v1-no-video-track", "v2-unknown-caption-style", "v1-gap"],
)
async def test_an_unplannable_edit_is_refused_and_reported(store, layout, plan_renders, timeline):
    await _seed(store, layout)
    before_clip = dict(await store.get_clip(CLIP_ID))
    await _save_edit(store, timeline)
    bus, recorder = _recording_bus()

    await orch._run_job(_trigger(), bus, store)

    assert plan_renders == []
    assert layout["output"].read_bytes() == b"old"
    assert await store.get_clip(CLIP_ID) == before_clip
    failures = _failures(recorder)
    assert len(failures) == 1
    assert failures[0]["clip_id"] == CLIP_ID
    assert failures[0]["reason"] == "refused"
    assert "cannot be rendered" in failures[0]["error"]
    assert EventType.CLIP_RENDERED not in recorder.types()


async def test_a_failed_rerender_is_reported(store, layout, render_calls, monkeypatch):
    await _seed(store, layout)

    def boom(*a, **k):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(orch.render_service, "render_clip", boom)
    bus, recorder = _recording_bus()

    await orch._run_job(_trigger(), bus, store)

    failures = _failures(recorder)
    assert [(f["clip_id"], f["reason"]) for f in failures] == [(CLIP_ID, "failed")]
    assert "ffmpeg exploded" in failures[0]["error"]
    assert EventType.JOB_FAILED not in recorder.types()


async def test_a_successful_rerender_reports_no_failure(store, layout, plan_renders):
    await _seed(store, layout)
    (layout["clips"] / "vo.wav").write_bytes(b"RIFF")
    await _save_edit(store, _v2_edit())
    bus, recorder = _recording_bus()

    await orch._run_job(_trigger(), bus, store)

    assert _failures(recorder) == []
    assert EventType.CLIP_RENDERED in recorder.types()


def _durations(monkeypatch, layout, reel_seconds: float) -> None:
    """The source probes 60 s; the rendered reel ``reel_seconds``."""
    output = str(layout["output"])

    def duration(path, *a, **k):
        return reel_seconds if str(path) == output else 60.0

    monkeypatch.setattr(orch.ffmpeg_tools, "duration", duration)


async def test_an_edited_rerender_records_the_real_reel_length(store, layout, plan_renders, monkeypatch):
    await _seed(store, layout)
    (layout["clips"] / "vo.wav").write_bytes(b"RIFF")
    await _save_edit(store, _v2_edit())
    _durations(monkeypatch, layout, 5.04)

    await orch._run_job(_trigger(), AsyncEventBus(), store)

    assert orch.clip_words.read_reel_duration(str(layout["output"])) == 5.04
    clip = await store.get_clip(CLIP_ID)
    # The clip keeps its window (it anchors a v1 edit) ...
    assert (clip["start"], clip["end"]) == (6.0, 12.0)
    # ... while the manifest reports the edited reel's real length.
    assert manifest_service._duration(clip) == 5.04


async def test_a_plain_rerender_forgets_a_recorded_reel_length(store, layout, plan_renders, monkeypatch):
    await _seed(store, layout)
    orch.clip_words.write_reel_duration(str(layout["output"]), 5.04)
    monkeypatch.setattr(orch.transcription_service, "transcribe_to_words", lambda *a, **k: [WordTiming("x", 0, 1)])

    await orch._run_job(_trigger(), AsyncEventBus(), store)

    assert orch.clip_words.read_reel_duration(str(layout["output"])) is None
    assert manifest_service._duration(await store.get_clip(CLIP_ID)) == 6.0


async def test_an_edited_rerender_never_reads_the_source_words_sidecar(store, layout, plan_renders, monkeypatch):
    await _seed(store, layout)
    (layout["clips"] / "vo.wav").write_bytes(b"RIFF")
    await _save_edit(store, _v2_edit())
    segment_discovery.write_words_sidecar(
        str(layout["source"]), [WordTiming("sidecar", 7.1, 7.5), WordTiming("words", 7.6, 8.0)]
    )
    reads: list[str] = []
    real = segment_discovery.read_words_sidecar

    def spy(path):
        reads.append(path)
        return real(path)

    monkeypatch.setattr(orch.segment_discovery, "read_words_sidecar", spy)

    await orch._run_job(_trigger(), AsyncEventBus(), store)

    assert reads == []
    (call,) = plan_renders
    assert call.get("word_timings") is None or [w.word for w in call["word_timings"]] == ["hello", "there"]
    assert [c.text for c in call["plan"].captions] == ["hello there"]
    clip = await store.get_clip(CLIP_ID)
    assert clip["transcript"] == "hello there"
    words = orch.clip_words.read_clip_words(str(layout["output"]))
    assert [w.word for w in words] == ["hello", "there"]
