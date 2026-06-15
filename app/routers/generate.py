"""Generate endpoint — stores a text brief and enqueues a generate:// job.

POST /generate writes the brief to ``settings.generate_brief_dir`` atomically
(tmp file + os.replace) and enqueues a job with ``url=generate://<brief_id>``
so the existing orchestrator/job-queue machinery drives it unchanged.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.domain.events import Event, EventType
from app.domain.ids import new_job_id
from app.domain.models import JobState, PipelineOptions
from app.settings import settings

router = APIRouter(prefix="/generate", tags=["generate"])


class GenerateShot(BaseModel):
    prompt: str = Field(..., max_length=2_000)
    seconds: float = Field(2.0, ge=0.1, le=30.0)


class GenerateBriefRequest(BaseModel):
    title: str = Field(..., max_length=200, min_length=1)
    script: str = Field(..., max_length=10_000, min_length=1)
    shots: list[GenerateShot] = Field(default_factory=list, max_length=50)
    voice_profile: str = Field("", max_length=200)
    music_url: str = Field("", max_length=2_000)

    @field_validator("music_url")
    @classmethod
    def _validate_music_url(cls, v: str) -> str:
        # Empty is allowed (no backing track); otherwise it must be a real
        # http(s) URL — reject data:, file:, javascript:, etc. with 422.
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("music_url must be empty or an http(s) URL")
        return v


class GenerateResponse(BaseModel):
    job_id: str
    brief_id: str


def _write_brief_atomic(brief_dir: str, brief_id: str, payload: dict) -> str:
    """Write the brief JSON atomically (tmp + os.replace). Returns the path."""
    directory = Path(brief_dir)
    directory.mkdir(parents=True, exist_ok=True)
    final_path = directory / f"{brief_id}.json"
    tmp_path = directory / f".{brief_id}.json.tmp"
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, final_path)
    return str(final_path)


@router.post("", response_model=GenerateResponse, status_code=202)
async def create_generate_job(
    req: GenerateBriefRequest, request: Request
) -> GenerateResponse:
    if not settings.generate_enabled:
        raise HTTPException(
            status_code=400,
            detail="generate mode disabled: set YTVIDEO_GENERATE_ENABLED=true",
        )

    brief_id = uuid4().hex
    _write_brief_atomic(
        settings.generate_brief_dir,
        brief_id,
        {
            "title": req.title,
            "script": req.script,
            # Shots are validated models now; serialize to plain dicts so the
            # brief JSON round-trips (the generate adapter reads dicts off disk).
            "shots": [shot.model_dump() for shot in req.shots],
            "voice_profile": req.voice_profile,
            "music_url": req.music_url,
        },
    )

    url = f"generate://{brief_id}"
    job_id = new_job_id()
    state = JobState(
        job_id=job_id,
        url=url,
        source="generate",
        download_path=settings.default_download_path,
        caption_format=settings.default_caption_format,
        target_aspect_ratio=settings.default_target_aspect_ratio,
        pipeline_options=PipelineOptions(),
    )
    await request.app.state.job_store.create(state)

    payload = {
        "url": url,
        "download_path": settings.default_download_path,
        "caption_format": settings.default_caption_format,
        "target_aspect_ratio": settings.default_target_aspect_ratio,
        "segment_mode": "auto",
        "language": settings.default_transcription_language,
        "prompt": None,
        "auto_hook": True,
        "brand_template_id": None,
        "pipeline_options": PipelineOptions().model_dump(),
    }
    # Enqueue via the job queue so concurrency is capped by YTVIDEO_MAX_CONCURRENT_JOBS.
    if hasattr(request.app.state, "job_queue"):
        await request.app.state.job_queue.put((job_id, payload))
    else:
        await request.app.state.event_bus.publish(
            Event(type=EventType.VIDEO_REQUESTED, job_id=job_id, payload=payload)
        )

    return GenerateResponse(job_id=job_id, brief_id=brief_id)
