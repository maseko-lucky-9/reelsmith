"""Reprompt clipping router (W1.10).

Re-runs the segment proposer for an existing job with a new prompt
and/or length range. Cheap — does not re-download or re-transcribe.

Per the plan, the actual segment_proposer re-run is delegated to a
background worker hook so this router stays small. We update the
job's pipeline_options + prompt and emit a `JOB_REPROMPTED` event;
the worker (or W3 scheduled refresh) picks it up.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JobRecord
from app.db.session import get_session

router = APIRouter(prefix="/api/jobs", tags=["reprompt"])


_LENGTH_RANGES = {
    "0-1m":   (0, 60),
    "1-3m":   (60, 180),
    "3-5m":   (180, 300),
    "5-10m":  (300, 600),
    "10-15m": (600, 900),
}


class RepromptRequest(BaseModel):
    prompt: str | None = Field(default=None, max_length=2000)
    length_range: str | None = None  # one of _LENGTH_RANGES keys
    length_min_seconds: int | None = Field(default=None, ge=0, le=3600)
    length_max_seconds: int | None = Field(default=None, ge=0, le=3600)

    @field_validator("length_range")
    @classmethod
    def _check_range(cls, v):
        if v is not None and v not in _LENGTH_RANGES:
            raise ValueError(
                f"length_range must be one of {sorted(_LENGTH_RANGES)}"
            )
        return v


def _resolve_range(body: RepromptRequest) -> tuple[int | None, int | None]:
    if body.length_range:
        return _LENGTH_RANGES[body.length_range]
    return body.length_min_seconds, body.length_max_seconds


@router.post("/{job_id}/reprompt")
async def reprompt_job(
    job_id: str,
    body: RepromptRequest,
    session: AsyncSession = Depends(get_session),
):
    res = await session.execute(select(JobRecord).where(JobRecord.id == job_id))
    job = res.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    lo, hi = _resolve_range(body)
    if lo is not None and hi is not None and lo > hi:
        raise HTTPException(
            status_code=422, detail="length_min_seconds must be <= length_max_seconds"
        )

    if body.prompt is not None:
        job.prompt = body.prompt
    options = dict(job.pipeline_options or {})
    if lo is not None:
        options["target_length_min_seconds"] = lo
    if hi is not None:
        options["target_length_max_seconds"] = hi
    # Reprompt = re-run segment proposer; everything else off so we don't
    # re-download / re-transcribe.
    options.update({
        "transcription": False,
        "captions": False,
        "render": False,
        "reframe": False,
        "broll": False,
        "thumbnail": False,
        "segment_proposer": True,
    })
    job.pipeline_options = options
    job.status = "pending"
    job.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(job)

    return {
        "job_id": job.id,
        "status": job.status,
        "prompt": job.prompt,
        "pipeline_options": job.pipeline_options,
    }
