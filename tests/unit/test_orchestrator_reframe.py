"""Reframe wiring in the orchestrator (FR-010, T012): ``_reframe_step``.

The face track runs only when the job's ``reframe`` option is on AND
``settings.reframe_provider == "face_track"``; its track goes to
``render_clip(crop_track=...)``. Detection is a real decode of a synthetic
source in a worker thread, with the detector factory replaced by a fake. Any
failure falls back to the letterbox render with ``StageSkipped(reframe)``;
cancellation propagates.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import InMemoryJobStore
from app.domain.events import Event, EventType
from app.domain.models import JobState, PipelineOptions
from app.services import face_detector, ffmpeg_tools
from app.services.face_detector import Face
from app.settings import Settings
from app.workers import orchestrator as orch
from tests.unit.fake_face_detector import (
    BlockDetector,
    FailingDetector,
    ScriptedDetector,
    write_block_video,
)

JOB_ID = "job-reframe"
CLIP_ID = "c1-0000-reframe"
_OPTS = PipelineOptions(audio_enhance=False, transcription=False, thumbnail=False)


@pytest.fixture(scope="module")
def source(tmp_path_factory) -> Path:
    """3 s at 24 fps; the white block moves right 4 px per frame."""
    path = tmp_path_factory.mktemp("orch_reframe") / "video.mp4"
    return write_block_video(path, block_left=lambda i: 4 * i)


@pytest.fixture
def render_calls(monkeypatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _render(video_path, output_path, start, end, *args, **kwargs):
        calls.append({"args": (video_path, start, end, *args), **kwargs})
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"reel")
        return output_path

    monkeypatch.setattr(orch.render_service, "render_clip", _render)
    return calls


@pytest.fixture
def use_detector(monkeypatch):
    """Install a fake as what the detector factory returns."""
    installed: dict[str, Any] = {"factory_calls": 0}

    def install(detector) -> None:
        def factory():
            installed["factory_calls"] += 1
            return detector

        monkeypatch.setattr(face_detector, "get_face_detector", factory)

    installed["install"] = install
    return installed


@pytest.fixture
def face_track(monkeypatch):
    monkeypatch.setattr(orch.settings, "reframe_provider", "face_track")


class _Bus(AsyncEventBus):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[Event] = []

    async def publish(self, event: Event) -> None:  # type: ignore[override]
        self.events.append(event)
        await super().publish(event)

    def reframe_skips(self) -> list[dict[str, Any]]:
        return [
            e.payload
            for e in self.events
            if e.type is EventType.STAGE_SKIPPED
            and e.payload.get("stage_id") == "reframe"
        ]


async def _store() -> InMemoryJobStore:
    store = InMemoryJobStore()
    await store.create(JobState(job_id=JOB_ID, url="https://x/v", download_path="/d"))
    return store


async def _chapter(tmp_path, source, bus, *, opts=_OPTS, store=None) -> str | None:
    store = store or await _store()
    cleanup = tmp_path / "tmp"
    cleanup.mkdir(exist_ok=True)
    return await orch._process_chapter(
        chapter={"index": 0, "title": "Talk", "start": 0.5, "end": 2.5},
        job_id=JOB_ID,
        video_path=str(source),
        clips_folder=str(tmp_path / "clips"),
        cleanup_root=cleanup,
        caption_format="srt",
        target_aspect_ratio=9 / 16,
        bus=bus,
        store=store,
        pipeline_options=opts,
    )


async def test_face_track_is_passed_to_the_render(
    tmp_path, source, render_calls, use_detector, face_track
):
    detector = BlockDetector()
    use_detector["install"](detector)
    bus = _Bus()

    await _chapter(tmp_path, source, bus)

    [call] = render_calls
    track = call["crop_track"]
    assert track is not None and len(track) >= 2
    xs = [x for _t, x in track]
    assert xs == sorted(xs) and xs[-1] > xs[0]  # follows the block to the right
    assert track[0][0] == 0.0  # clip time, not source time
    assert detector.calls == 5  # 2 fps over 2 s, plus the last frame
    assert bus.reframe_skips() == []
    assert any(e.type is EventType.CLIP_RENDERED for e in bus.events)


async def test_detection_runs_off_the_event_loop_thread(
    tmp_path, source, render_calls, use_detector, face_track
):
    detector = BlockDetector()
    use_detector["install"](detector)

    await _chapter(tmp_path, source, _Bus())

    assert detector.threads and threading.get_ident() not in detector.threads


async def test_detector_error_falls_back_to_the_letterbox(
    tmp_path, source, render_calls, use_detector, face_track
):
    use_detector["install"](FailingDetector())
    bus = _Bus()

    out = await _chapter(tmp_path, source, bus)

    assert out is not None
    [call] = render_calls
    assert call["crop_track"] is None
    [skip] = bus.reframe_skips()
    assert skip["chapter_index"] == 0
    assert skip["reason"] == "face tracking failed: detector exploded"
    assert any(e.type is EventType.CLIP_RENDERED for e in bus.events)


async def test_split_screen_falls_back_to_the_letterbox(
    tmp_path, source, render_calls, use_detector, face_track
):
    two_far_apart = [
        Face(x=40.0, y=150.0, w=60.0, h=60.0, score=0.9),
        Face(x=540.0, y=150.0, w=60.0, h=60.0, score=0.9),
    ]
    use_detector["install"](ScriptedDetector([two_far_apart]))
    bus = _Bus()

    await _chapter(tmp_path, source, bus)

    assert render_calls[0]["crop_track"] is None
    assert [s["reason"] for s in bus.reframe_skips()] == ["split screen"]


@pytest.mark.parametrize(
    ("provider", "opts"),
    [
        ("letterbox", _OPTS),
        ("stub", _OPTS),
        ("face_track", _OPTS.model_copy(update={"reframe": False})),
    ],
)
async def test_no_detection_unless_the_option_and_provider_ask_for_it(
    tmp_path, source, render_calls, use_detector, monkeypatch, provider, opts
):
    monkeypatch.setattr(orch.settings, "reframe_provider", provider)
    detector = BlockDetector()
    use_detector["install"](detector)
    bus = _Bus()

    await _chapter(tmp_path, source, bus, opts=opts)

    assert use_detector["factory_calls"] == 0 and detector.calls == 0
    [call] = render_calls
    assert call["crop_track"] is None  # the letterbox graph, byte-identical
    assert bus.reframe_skips() == []


async def test_cancellation_propagates_and_stops_the_decode(
    tmp_path, source, render_calls, use_detector, face_track
):
    started = threading.Event()

    class _Blocking(BlockDetector):
        def detect(self, bgr):
            started.set()
            cancel = ffmpeg_tools.current_cancel_event()
            assert cancel is not None
            cancel.wait(10)
            return super().detect(bgr)

    detector = _Blocking()
    use_detector["install"](detector)
    bus = _Bus()
    task = asyncio.create_task(_chapter(tmp_path, source, bus))
    assert await asyncio.to_thread(started.wait, 10)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert render_calls == []
    assert bus.reframe_skips() == []
    assert detector.calls == 1  # the worker stopped at the next frame


def test_reframe_provider_defaults_to_letterbox(monkeypatch):
    monkeypatch.delenv("YTVIDEO_REFRAME_PROVIDER", raising=False)

    assert Settings(_env_file=None).reframe_provider == "letterbox"


def test_reframe_model_dir_reads_its_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("YTVIDEO_REFRAME_MODEL_DIR", str(tmp_path))

    assert Settings(_env_file=None).reframe_model_dir == str(tmp_path)


# ── re-render goes through the same step ─────────────────────────────────────


async def test_rerender_uses_the_face_track(
    tmp_path, source, render_calls, use_detector, face_track, monkeypatch
):
    use_detector["install"](BlockDetector())
    monkeypatch.setattr(
        orch.thumbnail_service, "generate_thumbnail", lambda clip, out: out
    )
    clips = tmp_path / "clips"
    clips.mkdir()
    output = clips / "00_Talk.mp4"
    output.write_bytes(b"old")
    store = await _store()

    def _complete(s: JobState) -> None:
        s.status = "completed"
        s.video_path = str(source)

    await store.update(JOB_ID, _complete)
    await store.upsert_clip(
        JOB_ID,
        CLIP_ID,
        lambda c: c.update(
            {"start": 0.5, "end": 2.5, "output_path": str(output), "title": "Talk"}
        ),
    )
    trigger = Event(
        type=EventType.VIDEO_REQUESTED,
        job_id=JOB_ID,
        payload={
            "rerender_clip_id": CLIP_ID,
            "url": "https://x/v",
            "download_path": "/d",
            "pipeline_options": _OPTS.model_dump(),
            # The UI always sends this; the provider is a server setting.
            "reframe_provider": "letterbox",
        },
    )

    await orch._run_job(trigger, AsyncEventBus(), store)

    [call] = render_calls
    assert call["args"][:3] == (str(source), 0.5, 2.5)
    assert call["crop_track"] is not None and len(call["crop_track"]) >= 2
    assert output.read_bytes() == b"reel"
