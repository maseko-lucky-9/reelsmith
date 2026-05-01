from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.bus.job_store import JobNotFoundError
from app.domain.events import Event, EventType
from app.domain.ids import new_job_id
from app.domain.models import JobState
from app.settings import settings

router = APIRouter(prefix="/jobs", tags=["jobs"])


class CreateJobRequest(BaseModel):
    url: str
    download_path: str
    caption_format: str = Field(default_factory=lambda: settings.default_caption_format)
    target_aspect_ratio: float = Field(
        default_factory=lambda: settings.default_target_aspect_ratio
    )


class CreateJobResponse(BaseModel):
    job_id: str
    status: str


@router.post("", response_model=CreateJobResponse, status_code=202)
async def create_job(req: CreateJobRequest, request: Request) -> CreateJobResponse:
    job_id = new_job_id()
    state = JobState(
        job_id=job_id,
        url=req.url,
        download_path=req.download_path,
        caption_format=req.caption_format,
        target_aspect_ratio=req.target_aspect_ratio,
    )
    await request.app.state.job_store.create(state)
    await request.app.state.event_bus.publish(
        Event(
            type=EventType.VIDEO_REQUESTED,
            job_id=job_id,
            payload={
                "url": req.url,
                "download_path": req.download_path,
                "caption_format": req.caption_format,
                "target_aspect_ratio": req.target_aspect_ratio,
            },
        )
    )
    return CreateJobResponse(job_id=job_id, status="accepted")


@router.get("/{job_id}", response_model=JobState)
async def get_job(job_id: str, request: Request) -> JobState:
    try:
        return await request.app.state.job_store.get(job_id)
    except JobNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}") from e


_TERMINAL_TYPES = {EventType.JOB_COMPLETED, EventType.JOB_FAILED}


@router.get("/{job_id}/events")
async def stream_job_events(job_id: str, request: Request):
    bus = request.app.state.event_bus
    store = request.app.state.job_store

    try:
        await store.get(job_id)
    except JobNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}") from e

    async def event_source():
        async for event in bus.subscribe(job_id=job_id):
            yield {
                "event": event.type.value,
                "id": event.event_id,
                "data": json.dumps(event.to_dict()),
            }
            if event.type in _TERMINAL_TYPES:
                return

    return EventSourceResponse(event_source())
