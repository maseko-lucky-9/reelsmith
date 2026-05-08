from __future__ import annotations

import asyncio
import json
import subprocess

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.bus.job_store import JobNotFoundError
from app.domain.events import Event, EventType
from app.domain.ids import new_job_id
from app.domain.models import JobState
from app.services.platforms import detect_platform_id
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


class VideoPreviewResponse(BaseModel):
    title: str
    duration: float
    resolution: str


@router.get("/preview", response_model=VideoPreviewResponse)
async def preview_video(url: str) -> VideoPreviewResponse:
    """Fetch video metadata without downloading. Returns empty fields on failure."""
    def _fetch() -> dict:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-playlist", url],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {}
        return json.loads(result.stdout)

    try:
        info = await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=12)
    except Exception:
        info = {}

    height = info.get("height") or 0
    resolution = f"{height}p" if height else ""
    return VideoPreviewResponse(
        title=info.get("title", ""),
        duration=float(info.get("duration") or 0.0),
        resolution=resolution,
    )


@router.post("", response_model=CreateJobResponse, status_code=202)
async def create_job(req: CreateJobRequest, request: Request) -> CreateJobResponse:
    source = detect_platform_id(req.url)
    if source is None and not req.url.startswith("upload://"):
        raise HTTPException(
            status_code=400, detail=f"Unsupported platform for URL: {req.url}"
        )

    # Return existing job if the same URL was already processed or is running.
    existing = await request.app.state.job_store.list_jobs(limit=200)
    for job in existing:
        if job.url == req.url and job.status in ("completed", "running", "pending"):
            return CreateJobResponse(job_id=job.job_id, status=job.status)

    job_id = new_job_id()
    state = JobState(
        job_id=job_id,
        url=req.url,
        source=source,
        download_path=req.download_path,
        caption_format=req.caption_format,
        target_aspect_ratio=req.target_aspect_ratio,
    )
    await request.app.state.job_store.create(state)
    payload = {
        "url": req.url,
        "download_path": req.download_path,
        "caption_format": req.caption_format,
        "target_aspect_ratio": req.target_aspect_ratio,
    }
    # Enqueue via the job queue so concurrency is capped by YTVIDEO_MAX_CONCURRENT_JOBS.
    if hasattr(request.app.state, "job_queue"):
        await request.app.state.job_queue.put((job_id, payload))
    else:
        await request.app.state.event_bus.publish(
            Event(type=EventType.VIDEO_REQUESTED, job_id=job_id, payload=payload)
        )
    return CreateJobResponse(job_id=job_id, status="accepted")


@router.get("", response_model=list[JobState])
async def list_jobs(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    search: str = "",
) -> list[JobState]:
    return await request.app.state.job_store.list_jobs(
        limit=limit, offset=offset, search=search
    )


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
