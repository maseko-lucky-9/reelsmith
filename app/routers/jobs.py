from __future__ import annotations

import asyncio
import json
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.bus.job_store import JobNotFoundError
from app.domain.events import Event, EventType
from app.domain.ids import new_job_id
from app.domain.models import JobState, PipelineOptions
from app.services import retention
from app.services.platforms import detect_platform_id
from app.services.platforms.upload import uploaded_file
from app.services.yt_dlp_metadata import dump_json
from app.settings import settings
from app.workers import orchestrator

router = APIRouter(prefix="/jobs", tags=["jobs"])

# A new request for a URL with a job in one of these states reuses that job.
_DEDUP_STATUSES = ("completed", "running", "pending")

# Hard cap on a yt-dlp metadata lookup; the child process is killed after it.
_YT_DLP_TIMEOUT_SECONDS = 12.0

# A job in one of these states has (or is about to get) a pipeline task.
_ACTIVE_STATUSES = ("pending", "running")

# 409 details of DELETE /jobs/{id}; the web client tells them apart
# (web/src/lib/jobStatus.ts).
DELETE_WORK_RUNNING = (
    "a re-render, reprompt or clip enhancement of this job is still running; cancel it first"
)
DELETE_STILL_FINISHING = "the job is still finishing; try again in a moment"
DELETE_IN_PROGRESS = "this job is already being deleted"


class CreateJobRequest(BaseModel):
    url: str
    # Omitted by the UI: the server default keeps sources out of /tmp (T035).
    download_path: str = Field(default_factory=lambda: settings.default_download_path)
    caption_format: str = Field(default_factory=lambda: settings.default_caption_format)
    target_aspect_ratio: float = Field(
        default_factory=lambda: settings.default_target_aspect_ratio
    )
    segment_mode: Literal["auto", "chapter"] = "auto"
    language: str = "en-US"
    prompt: str | None = None
    auto_hook: bool = True
    brand_template_id: str | None = None
    pipeline_options: PipelineOptions = Field(default_factory=PipelineOptions)


class CreateJobResponse(BaseModel):
    job_id: str
    status: str


class VideoPreviewResponse(BaseModel):
    title: str
    duration: float
    resolution: str
    thumbnail: str = ""


@router.get("/preview", response_model=VideoPreviewResponse)
async def preview_video(url: str) -> VideoPreviewResponse:
    """Fetch video metadata without downloading. Returns empty fields on failure."""
    try:
        info = await dump_json(url, timeout=_YT_DLP_TIMEOUT_SECONDS)
    except Exception:
        info = {}

    height = info.get("height") or 0
    resolution = f"{height}p" if height else ""
    return VideoPreviewResponse(
        title=info.get("title", ""),
        duration=float(info.get("duration") or 0.0),
        resolution=resolution,
        thumbnail=info.get("thumbnail", ""),
    )


@router.get("/preview/thumbnail")
async def preview_thumbnail(url: str) -> Response:
    """Server-side proxy for video thumbnails (handles CDN referer restrictions)."""
    # Fetch thumbnail URL from yt-dlp metadata
    try:
        info = await dump_json(url, timeout=_YT_DLP_TIMEOUT_SECONDS)
    except Exception:
        raise HTTPException(status_code=404, detail="Could not resolve thumbnail")
    thumb_url = info.get("thumbnail", "")

    if not thumb_url:
        raise HTTPException(status_code=404, detail="No thumbnail available")

    # Determine referer from the original URL's platform homepage
    parsed = urlparse(url)
    referer = f"{parsed.scheme}://{parsed.hostname}/"

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.get(thumb_url, headers={"Referer": referer})
            resp.raise_for_status()
    except Exception:
        raise HTTPException(status_code=404, detail="Thumbnail fetch failed")

    content_type = resp.headers.get("content-type", "image/jpeg")
    return Response(
        content=resp.content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("", response_model=CreateJobResponse, status_code=202)
async def create_job(req: CreateJobRequest, request: Request) -> CreateJobResponse:
    source = detect_platform_id(req.url)
    if source is None and not req.url.startswith("upload://"):
        raise HTTPException(
            status_code=400, detail=f"Unsupported platform for URL: {req.url}"
        )
    # An upload:// URL must name an uploaded video in the uploads folder: a
    # path a client typed is never trusted (spec 013 threat T1).
    if req.url.startswith("upload://") and uploaded_file(req.url) is None:
        raise HTTPException(
            status_code=400,
            detail="upload:// URL must name an uploaded video file in the uploads folder",
        )

    # Return the existing job if the same URL was already processed or is
    # queued/running (exact URL match, newest first, any age).
    existing = await request.app.state.job_store.find_job_by_url(
        req.url, _DEDUP_STATUSES
    )
    if existing is not None:
        return CreateJobResponse(job_id=existing.job_id, status=existing.status)

    job_id = new_job_id()
    state = JobState(
        job_id=job_id,
        url=req.url,
        source=source,
        download_path=req.download_path,
        caption_format=req.caption_format,
        target_aspect_ratio=req.target_aspect_ratio,
        segment_mode=req.segment_mode,
        language=req.language,
        prompt=req.prompt,
        auto_hook=req.auto_hook,
        brand_template_id=req.brand_template_id,
        pipeline_options=req.pipeline_options,
    )
    await request.app.state.job_store.create(state)
    payload = {
        "url": req.url,
        "download_path": req.download_path,
        "caption_format": req.caption_format,
        "target_aspect_ratio": req.target_aspect_ratio,
        "segment_mode": req.segment_mode,
        "language": req.language,
        "prompt": req.prompt,
        "auto_hook": req.auto_hook,
        "brand_template_id": req.brand_template_id,
        "pipeline_options": req.pipeline_options.model_dump(),
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


class CancelJobResponse(BaseModel):
    job_id: str
    # "cancelling": its tasks were told to stop and report JobCancelled (or
    # RepromptFailed) as they unwind; "cancelled": it had no task, so it was
    # marked cancelled at once.
    status: Literal["cancelling", "cancelled"]


@router.post("/{job_id}/cancel", response_model=CancelJobResponse, status_code=202)
async def cancel_job(job_id: str, request: Request) -> CancelJobResponse:
    """Cancel a pending or running job, or a re-render or reprompt of it.

    The job ends ``cancelled`` (a re-render or reprompt leaves it
    ``completed`` with its clips). A pending or running job with no live
    task (still in the queue, or left by a process that died) is marked
    ``cancelled`` directly; a queued run that starts later sees that and
    does not run.

    Raises:
        HTTPException: 404 if the job is unknown; 409 if nothing of it is
            running (completed, failed or already cancelled).
    """
    store = request.app.state.job_store
    try:
        job = await store.get(job_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="job not found") from None

    if orchestrator.cancel_job(job_id):
        return CancelJobResponse(job_id=job_id, status="cancelling")
    if job.status not in _ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409, detail=f"job is {job.status}; nothing to cancel"
        )

    def _cancel(s: JobState) -> None:
        s.status = "cancelled"

    await store.update(job_id, _cancel)
    # The queued run may have got its task while the status was written: it
    # is cancelled too, and records "cancelled" again if it got as far as
    # "running". One that starts later sees the status and does not run.
    orchestrator.cancel_job(job_id)
    await request.app.state.event_bus.publish(
        Event(
            type=EventType.JOB_CANCELLED,
            job_id=job_id,
            payload={"cancelled_step": job.current_step},
        )
    )
    return CancelJobResponse(job_id=job_id, status="cancelled")


def _delete_lock(request: Request) -> asyncio.Lock:
    """One job delete at a time (per app): two jobs sharing a source, deleted
    together, would each see the other still naming it and both keep it."""
    lock = getattr(request.app.state, "job_delete_lock", None)
    if lock is None:
        lock = request.app.state.job_delete_lock = asyncio.Lock()
    return lock


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: str, request: Request) -> Response:
    """Delete a finished job: its files, then its rows (clips and everything
    hanging off them included).

    Files go first, so a crash part-way leaves a job that can be deleted
    again, never rows-less files. Only files below the managed folders are
    deleted, never an export copy, an ``inbox://`` job's files or a file
    another job still uses (``retention.delete_job_files``). Deleting a job
    that does not exist is not an error: the request is idempotent.

    The job is claimed as being deleted before the checks, so a re-render
    or reprompt asked for meanwhile answers 409; deletes run one at a time.

    Raises:
        HTTPException: 409, with a detail saying which, while the job is
            pending or running or a re-render, reprompt or clip enhancement
            of it runs (cancel it first); while only its worker threads are
            still finishing (try again in a moment); while another request
            is deleting it; or when its folder may not be deleted safely
            (then nothing is deleted, rows included).
    """
    store = request.app.state.job_store
    if not orchestrator.claim_delete(job_id):
        # A duplicate request: done if the other one already got there.
        try:
            await store.get(job_id)
        except JobNotFoundError:
            return Response(status_code=204)
        raise HTTPException(status_code=409, detail=DELETE_IN_PROGRESS)
    try:
        async with _delete_lock(request):
            try:
                job = await store.get(job_id)
            except JobNotFoundError:
                return Response(status_code=204)
            if job.status in _ACTIVE_STATUSES:
                raise HTTPException(status_code=409, detail=f"job is {job.status}; cancel it first")
            if orchestrator.job_has_work(job_id):
                raise HTTPException(status_code=409, detail=DELETE_WORK_RUNNING)
            if orchestrator.job_threads_running(job_id):
                raise HTTPException(status_code=409, detail=DELETE_STILL_FINISHING)
            try:
                await retention.delete_job_files(job, store)
            except retention.JobFolderRefused as refused:
                raise HTTPException(
                    status_code=409,
                    detail=f"the job's folder cannot be deleted safely ({refused}); nothing was deleted",
                ) from None
            await store.delete(job_id)
            await request.app.state.event_bus.forget(job_id)
    finally:
        orchestrator.release_delete(job_id)
    return Response(status_code=204)


# The stream ends after one of these. A failed reprompt ends with
# RepromptFailed (the job stays completed, so no JobCompleted/JobFailed).
_TERMINAL_TYPES = {
    EventType.JOB_COMPLETED,
    EventType.JOB_FAILED,
    EventType.JOB_CANCELLED,
    EventType.REPROMPT_FAILED,
}


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

    return EventSourceResponse(event_source(), ping=settings.sse_keepalive_seconds)
