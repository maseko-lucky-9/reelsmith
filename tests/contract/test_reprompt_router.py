"""Contract tests for ``POST /jobs/{id}/reprompt`` (FR-016, T008).

Order: invalid body -> 422; unknown job -> 404; min > max length -> 422;
job not completed, source video not retained, clip discovery off (without an
explicit range) or a reprompt of the job already in flight -> 409; otherwise
202 and one queued ``{"reprompt": True, ...}`` item. The job stays
``completed``; nothing is recorded on it until the reprompt succeeds.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import SqlJobStore
from app.db.base import Base
from app.domain.events import Event, EventType
from app.domain.models import JobState, PipelineOptions
from app.main import create_app
from app.services.segment_proposer import ProposedSegment
from app.services.transcription_service import WordTiming
from app.workers import orchestrator as orch

JOB_URL = "https://www.youtube.com/watch?v=reprompt01"

# PipelineOptions switches that turn a stage on or off.
_STAGE_FLAGS = (
    "transcription",
    "captions",
    "render",
    "segment_proposer",
    "reframe",
    "broll",
    "thumbnail",
    "ai_hook",
    "audio_enhance",
    "filler_removal",
)


@pytest.fixture(autouse=True)
def _no_reprompt_in_flight(monkeypatch: pytest.MonkeyPatch):
    orch._reprompts_in_flight.clear()
    monkeypatch.setattr(orch.settings, "segment_provider", "local_heuristic")
    yield
    orch._reprompts_in_flight.clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        # Nothing consumes this queue, so nothing runs; the test inspects it.
        test_client.app.state.job_queue = asyncio.Queue()
        yield test_client


@pytest.fixture
def source_video(tmp_path: Path) -> Path:
    video = tmp_path / "source.mp4"
    video.write_bytes(b"\x00" * 64)
    return video


def _seed(
    client: TestClient,
    *,
    status: str = "completed",
    video_path: str | None = None,
    duration: float | None = 360.0,
) -> None:
    store = client.app.state.job_store
    client.portal.call(
        store.create,
        JobState(
            job_id="job-1",
            url=JOB_URL,
            download_path="/downloads",
            caption_format="vtt",
            language="fr-FR",
            prompt="orig",
            pipeline_options=PipelineOptions(ai_hook=True),
        ),
    )

    def _mutate(s: JobState) -> None:
        s.status = status  # type: ignore[assignment]
        s.video_path = video_path
        s.duration = duration

    client.portal.call(store.update, "job-1", _mutate)


def _queue(client: TestClient) -> asyncio.Queue[tuple[str, dict[str, Any]]]:
    return client.app.state.job_queue


def _job(client: TestClient) -> JobState:
    return client.portal.call(client.app.state.job_store.get, "job-1")


# ── 202 ───────────────────────────────────────────────────────────────────────


def test_reprompt_queues_one_item_and_leaves_the_job_completed(client, source_video):
    _seed(client, video_path=str(source_video))

    r = client.post(
        "/jobs/job-1/reprompt",
        json={"prompt": "new prompt", "length_range": "1-3m"},
    )

    assert r.status_code == 202
    body = r.json()
    assert body["job_id"] == "job-1"
    assert body["status"] == "queued"
    assert body["prompt"] == "new prompt"
    opts = body["pipeline_options"]
    assert opts["target_length_min_seconds"] == 60
    assert opts["target_length_max_seconds"] == 180
    assert _queue(client).qsize() == 1
    job_id, payload = _queue(client).get_nowait()
    assert job_id == "job-1"
    assert payload["reprompt"] is True
    assert payload["prompt"] == "new prompt"
    assert payload["target_length_min_seconds"] == 60
    assert payload["target_length_max_seconds"] == 180
    assert payload["start_seconds"] is None and payload["end_seconds"] is None
    assert payload["caption_format"] == "vtt"
    assert payload["language"] == "fr-FR"
    assert "rerender_clip_id" not in payload
    # The job is not touched until the reprompt succeeds.
    job = _job(client)
    assert job.status == "completed"
    assert job.prompt == "orig"
    assert job.pipeline_options == PipelineOptions(ai_hook=True)


def test_reprompt_with_explicit_seconds(client, source_video):
    _seed(client, video_path=str(source_video))

    r = client.post(
        "/jobs/job-1/reprompt",
        json={"length_min_seconds": 30, "length_max_seconds": 90},
    )

    assert r.status_code == 202
    opts = r.json()["pipeline_options"]
    assert opts["target_length_min_seconds"] == 30
    assert opts["target_length_max_seconds"] == 90
    # Stage switches are not the reprompt's to change (T028 a2).
    assert {k: opts[k] for k in _STAGE_FLAGS} == {
        k: v
        for k, v in PipelineOptions(ai_hook=True).model_dump().items()
        if k in _STAGE_FLAGS
    }


def test_reprompt_keeps_the_prompt_when_not_provided(client, source_video):
    _seed(client, video_path=str(source_video))

    r = client.post("/jobs/job-1/reprompt", json={"length_range": "0-1m"})

    assert r.status_code == 202
    assert r.json()["prompt"] == "orig"
    _, payload = _queue(client).get_nowait()
    assert payload["prompt"] == "orig"


def test_reprompt_with_a_time_range_queues_it(client, source_video):
    _seed(client, video_path=str(source_video))

    r = client.post(
        "/jobs/job-1/reprompt", json={"start_seconds": 12.5, "end_seconds": 40}
    )

    assert r.status_code == 202
    _, payload = _queue(client).get_nowait()
    assert (payload["start_seconds"], payload["end_seconds"]) == (12.5, 40.0)


def test_reprompt_with_a_time_range_works_without_clip_discovery(
    client, source_video, monkeypatch
):
    monkeypatch.setattr(orch.settings, "segment_provider", "chapter")
    _seed(client, video_path=str(source_video))

    r = client.post(
        "/jobs/job-1/reprompt", json={"start_seconds": 0, "end_seconds": 30}
    )

    assert r.status_code == 202


def test_reprompt_forgets_the_old_run_in_the_replay_history(client, source_video):
    """Else an SSE stream opened right after the 202 (before the reprompt
    leaves the queue) replays the old JobCompleted and closes at once."""
    _seed(client, video_path=str(source_video))
    bus: AsyncEventBus = client.app.state.event_bus
    client.portal.call(bus.publish, Event(type=EventType.JOB_COMPLETED, job_id="job-1"))

    r = client.post("/jobs/job-1/reprompt", json={"prompt": "x"})

    assert r.status_code == 202
    assert client.portal.call(_replayed, bus, "job-1") == []


async def _replayed(bus: AsyncEventBus, job_id: str) -> list[Event]:
    received: list[Event] = []

    async def consume():
        async for event in bus.subscribe(job_id=job_id):
            received.append(event)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return received


def test_a_completed_jobs_url_still_dedups_during_a_reprompt(client, source_video):
    _seed(client, video_path=str(source_video))
    assert client.post("/jobs/job-1/reprompt", json={"prompt": "x"}).status_code == 202

    r = client.post("/jobs", json={"url": JOB_URL})

    assert r.status_code == 202
    assert r.json() == {"job_id": "job-1", "status": "completed"}


# ── 404 / 409 ─────────────────────────────────────────────────────────────────


def test_reprompt_unknown_job_404(client):
    r = client.post("/jobs/missing/reprompt", json={"prompt": "x"})

    assert r.status_code == 404
    assert _queue(client).qsize() == 0


@pytest.mark.parametrize("status", ["pending", "running", "failed"])
def test_reprompt_job_not_completed_409(client, source_video, status):
    _seed(client, status=status, video_path=str(source_video))

    r = client.post("/jobs/job-1/reprompt", json={"prompt": "x"})

    assert r.status_code == 409
    assert "completed" in r.json()["detail"]
    assert _queue(client).qsize() == 0
    assert _job(client).status == status


@pytest.mark.parametrize("missing", ["unset", "deleted"])
def test_reprompt_source_not_retained_409(client, tmp_path, missing):
    path = None if missing == "unset" else str(tmp_path / "gone.mp4")
    _seed(client, video_path=path)

    r = client.post("/jobs/job-1/reprompt", json={"prompt": "x"})

    assert r.status_code == 409
    assert r.json()["detail"] == "source video not retained"
    assert _queue(client).qsize() == 0


def test_reprompt_without_clip_discovery_409(client, source_video, monkeypatch):
    monkeypatch.setattr(orch.settings, "segment_provider", "chapter")
    _seed(client, video_path=str(source_video))

    r = client.post("/jobs/job-1/reprompt", json={"prompt": "x"})

    assert r.status_code == 409
    assert "segment_provider" in r.json()["detail"]
    assert _queue(client).qsize() == 0


def test_second_reprompt_while_one_is_in_flight_409(client, source_video):
    _seed(client, video_path=str(source_video))
    assert client.post("/jobs/job-1/reprompt", json={"prompt": "a"}).status_code == 202

    r = client.post("/jobs/job-1/reprompt", json={"prompt": "b"})

    assert r.status_code == 409
    assert "already" in r.json()["detail"]
    assert _queue(client).qsize() == 1


def test_a_rejected_reprompt_does_not_claim_the_job(client, tmp_path, source_video):
    _seed(client, video_path=str(tmp_path / "gone.mp4"))
    assert client.post("/jobs/job-1/reprompt", json={}).status_code == 409

    assert not orch.reprompt_in_flight("job-1")


# ── 422 ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        {"length_range": "9-99h"},
        {"length_min_seconds": 200, "length_max_seconds": 100},
        {"length_min_seconds": -1},
        {"prompt": "x" * 2001},
        {"start_seconds": 10},
        {"end_seconds": 10},
        {"start_seconds": 30, "end_seconds": 30},
        {"start_seconds": 40, "end_seconds": 30},
        {"start_seconds": -5, "end_seconds": 30},
        {"start_seconds": 400, "end_seconds": 430},  # starts past the 360 s source
    ],
)
def test_reprompt_invalid_body_422(client, source_video, body):
    _seed(client, video_path=str(source_video))

    r = client.post("/jobs/job-1/reprompt", json=body)

    assert r.status_code == 422, r.json()
    assert _queue(client).qsize() == 0
    assert not orch.reprompt_in_flight("job-1")


# ── run through the orchestrator (SQL store) ──────────────────────────────────


@pytest.fixture
async def sql_env(tmp_path, monkeypatch):
    """A completed job (saved source, one clip, words sidecar) in a SQL store
    behind the app, plus fakes for every service a reprompt runs."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    store = SqlJobStore()
    store._factory = async_sessionmaker(engine, expire_on_commit=False)

    clips = tmp_path / "vid" / "clips"
    clips.mkdir(parents=True)
    source = tmp_path / "vid" / "video.mp4"
    source.write_bytes(b"source")
    output = clips / "00_Intro.mp4"
    output.write_bytes(b"old")
    original = PipelineOptions(audio_enhance=False, ai_hook=True)
    await store.create(
        JobState(
            job_id="job-r",
            url="https://www.youtube.com/watch?v=reprompt02",
            download_path="/downloads",
            prompt="orig",
            pipeline_options=original,
        )
    )

    def _complete(s: JobState) -> None:
        s.status = "completed"
        s.video_path = str(source)

    await store.update("job-r", _complete)
    await store.upsert_clip(
        "job-r",
        "clip-r",
        lambda c: c.update(
            {"start": 0.0, "end": 4.0, "output_path": str(output), "title": "Intro"}
        ),
    )

    renders: list[str | None] = []

    def _render(video_path, output_path, start, end, captions_path, *args, **kwargs):
        renders.append(captions_path)
        Path(output_path).write_bytes(b"new")
        return output_path

    def _extract_audio(src, start, duration, wav_path):
        Path(wav_path).write_bytes(b"\x00")
        return wav_path

    class _Proposer:
        def propose(self, words, audio_path, chapters, duration, *, prompt=None):
            return [ProposedSegment(start=10.0, end=40.0, title="Fresh", score=50)]

    monkeypatch.setattr(orch.render_service, "render_clip", _render)
    monkeypatch.setattr(orch.clip_service, "extract_audio", _extract_audio)
    monkeypatch.setattr(orch.clip_service, "probe_safe_end", lambda path: 120.0)
    monkeypatch.setattr(
        orch.thumbnail_service, "generate_thumbnail", lambda clip, out: out
    )
    monkeypatch.setattr(
        orch.transcription_service,
        "transcribe_to_words",
        lambda audio_path, **_: [WordTiming("hello", 10.0, 10.5)],
    )
    monkeypatch.setattr(
        orch.segment_proposer, "get_segment_proposer", lambda **_: _Proposer()
    )
    monkeypatch.setattr(orch.settings, "ollama_enabled", False)
    monkeypatch.setattr(orch.settings, "export_base_folder", str(tmp_path / "exp"))

    # No lifespan: the app runs on this test's loop, like the SQL engine.
    app = create_app()
    app.state.job_store = store
    app.state.event_bus = AsyncEventBus()
    app.state.job_queue = asyncio.Queue()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.app = app  # type: ignore[attr-defined]
        yield client, store, original, renders

    await engine.dispose()


async def _run_queued(client: httpx.AsyncClient, store: Any) -> None:
    job_id, payload = client.app.state.job_queue.get_nowait()
    await orch._run_job(
        Event(type=EventType.VIDEO_REQUESTED, job_id=job_id, payload=payload),
        AsyncEventBus(),
        store,
    )


def _rerender_trigger(job: JobState, clip_id: str) -> Event:
    """The payload ``POST /clips/{id}/rerender`` queues for this job."""
    return Event(
        type=EventType.VIDEO_REQUESTED,
        job_id=job.job_id,
        payload={
            "rerender_clip_id": clip_id,
            "url": job.url,
            "download_path": job.download_path,
            "caption_format": job.caption_format,
            "target_aspect_ratio": job.target_aspect_ratio,
            "language": job.language,
            "pipeline_options": job.pipeline_options.model_dump(),
            "reframe_provider": "letterbox",
        },
    )


async def test_reprompt_does_not_persist_render_false(sql_env):
    """T028 (a2): reprompt used to save ``render/captions/transcription=False``
    into the job's options, and a later clip re-render reused them, so that
    re-render ran without captions."""
    client, store, original, renders = sql_env

    r = await client.post("/jobs/job-r/reprompt", json={"prompt": "new prompt"})
    assert r.status_code == 202
    await _run_queued(client, store)

    job = await store.get("job-r")
    assert job.status == "completed"
    assert job.pipeline_options == original
    assert job.prompt == "new prompt"
    (fresh,) = await store.list_clips(job_id="job-r")
    assert fresh["title"] == "Fresh"
    assert (await store.get_clip("clip-r")) is None  # retired

    renders.clear()
    await orch._run_job(
        _rerender_trigger(job, fresh["clip_id"]), AsyncEventBus(), store
    )

    assert len(renders) == 1
    assert renders[0] is not None and renders[0].endswith(".srt")
    clip = await store.get_clip(fresh["clip_id"])
    assert clip["transcript"] == "hello"


async def test_reprompt_range_leaves_stage_switches_alone(sql_env):
    client, store, original, _ = sql_env

    r = await client.post("/jobs/job-r/reprompt", json={"length_range": "1-3m"})
    assert r.status_code == 202
    await _run_queued(client, store)

    stored = (await store.get("job-r")).pipeline_options.model_dump()
    assert stored == {
        **original.model_dump(),
        "target_length_min_seconds": 60,
        "target_length_max_seconds": 180,
    }
    assert not orch.reprompt_in_flight("job-r")
