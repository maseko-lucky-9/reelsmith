"""Pipeline options round-trip through InMemoryJobStore + G4 null coercion."""
from __future__ import annotations

import pytest

from app.bus.job_store import InMemoryJobStore
from app.domain.models import JobState, PipelineOptions


@pytest.mark.asyncio
async def test_pipeline_options_roundtrip_defaults():
    """Default PipelineOptions survives create→get."""
    store = InMemoryJobStore()
    state = JobState(
        job_id="job-po-1",
        url="https://www.youtube.com/watch?v=abc",
        download_path="/tmp",
    )
    await store.create(state)
    fetched = await store.get("job-po-1")
    assert fetched.pipeline_options.transcription is True
    assert fetched.pipeline_options.render is True
    assert fetched.pipeline_options.thumbnail is True
    assert fetched.segment_mode == "auto"
    assert fetched.language == "en-US"
    assert fetched.auto_hook is True
    assert fetched.prompt is None
    assert fetched.brand_template_id is None


@pytest.mark.asyncio
async def test_pipeline_options_roundtrip_custom():
    """Custom PipelineOptions survives create→get."""
    store = InMemoryJobStore()
    opts = PipelineOptions(
        transcription=False,
        captions=False,
        render=True,
        segment_proposer=False,
        reframe=False,
        broll=False,
        thumbnail=True,
    )
    state = JobState(
        job_id="job-po-2",
        url="https://www.tiktok.com/@u/video/1",
        download_path="/tmp",
        source="tiktok",
        segment_mode="chapter",
        language="fr-FR",
        prompt="test prompt",
        auto_hook=False,
        brand_template_id="tmpl-123",
        pipeline_options=opts,
    )
    await store.create(state)
    fetched = await store.get("job-po-2")
    assert fetched.pipeline_options.transcription is False
    assert fetched.pipeline_options.captions is False
    assert fetched.pipeline_options.render is True
    assert fetched.pipeline_options.segment_proposer is False
    assert fetched.pipeline_options.reframe is False
    assert fetched.pipeline_options.broll is False
    assert fetched.pipeline_options.thumbnail is True
    assert fetched.segment_mode == "chapter"
    assert fetched.language == "fr-FR"
    assert fetched.prompt == "test prompt"
    assert fetched.auto_hook is False
    assert fetched.brand_template_id == "tmpl-123"


@pytest.mark.asyncio
async def test_pipeline_options_survives_update():
    """PipelineOptions persists across update mutations."""
    store = InMemoryJobStore()
    opts = PipelineOptions(render=False, thumbnail=False)
    state = JobState(
        job_id="job-po-3",
        url="https://www.youtube.com/watch?v=xyz",
        download_path="/tmp",
        pipeline_options=opts,
    )
    await store.create(state)
    await store.update("job-po-3", lambda s: setattr(s, "status", "running"))
    fetched = await store.get("job-po-3")
    assert fetched.pipeline_options.render is False
    assert fetched.pipeline_options.thumbnail is False
    assert fetched.status == "running"


@pytest.mark.asyncio
async def test_null_pipeline_options_coerces_to_defaults():
    """Simulates a pre-migration row where pipeline_options is None (G4)."""
    from app.bus.job_store import _record_to_state

    class FakeRecord:
        id = "job-old"
        youtube_url = "https://www.youtube.com/watch?v=old"
        source = "youtube"
        status = "completed"
        error = None
        clips = []
        # Simulate old row — no pipeline_options column
        pipeline_options = None
        segment_mode = None
        language = None
        prompt = None
        auto_hook = None
        brand_template_id = None

    state = _record_to_state(FakeRecord())
    assert state.pipeline_options.transcription is True
    assert state.pipeline_options.render is True
    assert state.pipeline_options.captions is True
    assert state.segment_mode == "auto"
    assert state.language == "en-US"
    assert state.auto_hook is True


@pytest.mark.asyncio
async def test_pipeline_options_model_serialisation():
    """PipelineOptions model_dump produces a dict that round-trips."""
    opts = PipelineOptions(transcription=False, broll=False)
    d = opts.model_dump()
    restored = PipelineOptions(**d)
    assert restored.transcription is False
    assert restored.broll is False
    assert restored.render is True  # default
