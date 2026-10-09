"""Reprompt clipping router (W1.10, FR-016).

``POST /jobs/{id}/reprompt`` re-discovers the clips of a completed job from
its retained source with a new prompt, clip length range or time range. The
router validates and queues; ``orchestrator._reprompt_job`` does the work
(no re-download; the source's words are reused from the sidecar).

The job stays ``completed`` throughout: nothing is recorded on it until the
reprompt succeeds, and then only the prompt and the length range. The job's
stage switches (render, captions, transcription, ...) are never changed here:
a later clip re-render reuses the saved options, and a persisted
``render=False``/``captions=False`` made it lose its captions (task T028 a2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.bus.job_store import JobNotFoundError
from app.domain.events import Event, EventType
from app.workers import orchestrator

router = APIRouter(prefix="/jobs", tags=["reprompt"])


_LENGTH_RANGES = {
    "0-1m": (0, 60),
    "1-3m": (60, 180),
    "3-5m": (180, 300),
    "5-10m": (300, 600),
    "10-15m": (600, 900),
}


class RepromptRequest(BaseModel):
    prompt: str | None = Field(default=None, max_length=2000)
    length_range: str | None = None  # one of _LENGTH_RANGES keys
    length_min_seconds: int | None = Field(default=None, ge=0, le=3600)
    length_max_seconds: int | None = Field(default=None, ge=0, le=3600)
    # Optional time range of the source: one clip of exactly this range,
    # instead of the proposer's picks. Both or neither.
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)

    @field_validator("length_range")
    @classmethod
    def _check_range(cls, v):
        if v is not None and v not in _LENGTH_RANGES:
            raise ValueError(f"length_range must be one of {sorted(_LENGTH_RANGES)}")
        return v

    @model_validator(mode="after")
    def _check_span(self) -> RepromptRequest:
        if (self.start_seconds is None) != (self.end_seconds is None):
            raise ValueError("start_seconds and end_seconds go together")
        if self.start_seconds is not None and self.start_seconds >= self.end_seconds:
            raise ValueError("start_seconds must be < end_seconds")
        return self


def _resolve_range(body: RepromptRequest) -> tuple[int | None, int | None]:
    if body.length_range:
        return _LENGTH_RANGES[body.length_range]
    return body.length_min_seconds, body.length_max_seconds


@router.post("/{job_id}/reprompt", status_code=202)
async def reprompt_job(
    job_id: str, body: RepromptRequest, request: Request
) -> dict[str, Any]:
    """Queue a reprompt of a completed job.

    Raises:
        HTTPException: 404 if the job is unknown; 422 if the length range is
            inverted or the time range starts past the source's end; 409 if
            the job is not completed, its source video is gone, clip
            discovery is off (unless a time range is given), or a reprompt
            of this job is already in flight.
    """
    store = request.app.state.job_store
    try:
        job = await store.get(job_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="job not found") from None

    lo, hi = _resolve_range(body)
    if lo is not None and hi is not None and lo > hi:
        raise HTTPException(
            status_code=422, detail="length_min_seconds must be <= length_max_seconds"
        )
    has_span = body.start_seconds is not None
    if has_span and job.duration and body.start_seconds >= job.duration:
        raise HTTPException(
            status_code=422,
            detail=f"start_seconds is past the end of the source ({job.duration:.1f}s)",
        )

    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"job is {job.status}; only a completed job can be reprompted",
        )
    # Same rule and message as a clip re-render (POST /clips/{id}/rerender).
    if not job.video_path or not Path(job.video_path).is_file():
        raise HTTPException(status_code=409, detail="source video not retained")
    if not has_span and (reason := orchestrator.reprompt_unavailable_reason()):
        raise HTTPException(status_code=409, detail=reason)
    if not orchestrator.claim_reprompt(job_id):
        raise HTTPException(
            status_code=409, detail="a reprompt of this job is already running"
        )

    prompt = body.prompt if body.prompt is not None else job.prompt
    payload = {
        "reprompt": True,
        "prompt": prompt,
        "target_length_min_seconds": lo,
        "target_length_max_seconds": hi,
        "start_seconds": body.start_seconds,
        "end_seconds": body.end_seconds,
        "url": job.url,
        "download_path": job.download_path,
        "caption_format": job.caption_format,
        "target_aspect_ratio": job.target_aspect_ratio,
        "language": job.language,
    }
    try:
        # A stream opened from now on must not be replayed the old run's
        # JobCompleted (the SSE route closes on it) while this waits in line.
        await request.app.state.event_bus.forget(job_id)
        if hasattr(request.app.state, "job_queue"):
            await request.app.state.job_queue.put((job_id, payload))
        else:
            await request.app.state.event_bus.publish(
                Event(type=EventType.VIDEO_REQUESTED, job_id=job_id, payload=payload)
            )
    except BaseException:
        orchestrator.release_reprompt(job_id)
        raise

    # The options the reprompt runs with; recorded on the job when it succeeds.
    options = job.pipeline_options.model_dump()
    if lo is not None:
        options["target_length_min_seconds"] = lo
    if hi is not None:
        options["target_length_max_seconds"] = hi
    return {
        "job_id": job_id,
        "status": "queued",
        "prompt": prompt,
        "pipeline_options": options,
    }
