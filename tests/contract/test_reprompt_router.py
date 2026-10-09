"""Contract tests for /api/jobs/{id}/reprompt (W1.10)."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import SqlJobStore
from app.db.base import Base
from app.db.models import JobRecord
from app.db.session import get_session
from app.domain.events import Event, EventType
from app.domain.models import JobState, PipelineOptions
from app.main import create_app
from app.services.transcription_service import WordTiming
from app.workers import orchestrator as orch

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


@pytest.fixture
async def reprompt_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        job = JobRecord(youtube_url="https://example.com/v", prompt="orig")
        session.add(job)
        await session.commit()
        jid = job.id

    async def _override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, jid

    await engine.dispose()


async def test_reprompt_with_named_range(reprompt_client):
    client, jid = reprompt_client
    r = await client.post(
        f"/api/jobs/{jid}/reprompt",
        json={"prompt": "new prompt", "length_range": "1-3m"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["prompt"] == "new prompt"
    opts = body["pipeline_options"]
    assert opts["target_length_min_seconds"] == 60
    assert opts["target_length_max_seconds"] == 180
    # Stage switches are not the reprompt's to change (T028 a2).
    assert not set(opts) & set(_STAGE_FLAGS)
    assert body["status"] == "pending"


async def test_reprompt_with_explicit_seconds(reprompt_client):
    client, jid = reprompt_client
    r = await client.post(
        f"/api/jobs/{jid}/reprompt",
        json={"length_min_seconds": 30, "length_max_seconds": 90},
    )
    assert r.status_code == 200
    opts = r.json()["pipeline_options"]
    assert opts["target_length_min_seconds"] == 30
    assert opts["target_length_max_seconds"] == 90


async def test_reprompt_invalid_range_name_422(reprompt_client):
    client, jid = reprompt_client
    r = await client.post(
        f"/api/jobs/{jid}/reprompt",
        json={"length_range": "9-99h"},
    )
    assert r.status_code == 422


async def test_reprompt_min_greater_than_max_422(reprompt_client):
    client, jid = reprompt_client
    r = await client.post(
        f"/api/jobs/{jid}/reprompt",
        json={"length_min_seconds": 200, "length_max_seconds": 100},
    )
    assert r.status_code == 422


async def test_reprompt_unknown_job_404(reprompt_client):
    client, _ = reprompt_client
    r = await client.post("/api/jobs/missing/reprompt", json={"prompt": "x"})
    assert r.status_code == 404


async def test_reprompt_preserves_prompt_when_not_provided(reprompt_client):
    client, jid = reprompt_client
    r = await client.post(
        f"/api/jobs/{jid}/reprompt",
        json={"length_range": "0-1m"},
    )
    assert r.status_code == 200
    assert r.json()["prompt"] == "orig"


@pytest.fixture
async def rerender_env(tmp_path, monkeypatch):
    """A completed job (saved source, one clip) in a SQL store whose engine
    also backs the reprompt route, plus render stubs that record the captions
    path each render got."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SqlJobStore()
    store._factory = factory

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
            url="https://www.youtube.com/watch?v=reprompt01",
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

    monkeypatch.setattr(orch.render_service, "render_clip", _render)
    monkeypatch.setattr(orch.clip_service, "extract_audio", _extract_audio)
    monkeypatch.setattr(
        orch.thumbnail_service, "generate_thumbnail", lambda clip, out: out
    )
    monkeypatch.setattr(
        orch.transcription_service,
        "transcribe_to_words",
        lambda audio_path, **_: [WordTiming("hello", 0.0, 0.5)],
    )

    async def _override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, store, original, renders

    await engine.dispose()


def _rerender_trigger(job: JobState) -> Event:
    """The payload ``POST /clips/{id}/rerender`` queues for this job."""
    return Event(
        type=EventType.VIDEO_REQUESTED,
        job_id=job.job_id,
        payload={
            "rerender_clip_id": "clip-r",
            "url": job.url,
            "download_path": job.download_path,
            "caption_format": job.caption_format,
            "target_aspect_ratio": job.target_aspect_ratio,
            "language": job.language,
            "pipeline_options": job.pipeline_options.model_dump(),
            "reframe_provider": "letterbox",
        },
    )


async def test_reprompt_does_not_persist_render_false(rerender_env):
    """T028 (a2): reprompt used to save ``render/captions/transcription=False``
    into the job's options, and a later clip re-render reused them, so that
    re-render ran without captions."""
    client, store, original, renders = rerender_env

    r = await client.post("/api/jobs/job-r/reprompt", json={"prompt": "new prompt"})
    assert r.status_code == 200

    job = await store.get("job-r")
    assert job.pipeline_options == original
    assert job.prompt == "new prompt"

    await orch._run_job(_rerender_trigger(job), AsyncEventBus(), store)

    assert len(renders) == 1
    assert renders[0] is not None and renders[0].endswith(".srt")
    clip = await store.get_clip("clip-r")
    assert clip["transcript"] == "hello"


async def test_reprompt_range_leaves_stage_switches_alone(rerender_env):
    client, store, original, _ = rerender_env

    r = await client.post("/api/jobs/job-r/reprompt", json={"length_range": "1-3m"})
    assert r.status_code == 200

    stored = (await store.get("job-r")).pipeline_options.model_dump()
    assert stored == {
        **original.model_dump(),
        "target_length_min_seconds": 60,
        "target_length_max_seconds": 180,
    }
