from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.bus.job_store import JobNotFoundError
from app.domain.events import Event, EventType

router = APIRouter(prefix="/clips", tags=["clips"])


@router.get("", response_model=list[dict[str, Any]])
async def list_clips(
    request: Request,
    job_id: str | None = None,
    min_score: int | None = None,
    search: str = "",
) -> list[dict[str, Any]]:
    return await request.app.state.job_store.list_clips(
        job_id=job_id, min_score=min_score, search=search
    )


@router.patch("/{clip_id}/like")
async def like_clip(clip_id: str, request: Request) -> dict[str, Any]:
    store = request.app.state.job_store
    clip = await store.get_clip(clip_id, include_retired=True)
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")
    new_liked = not clip.get("liked", False)

    async def _toggle(c: dict[str, Any]) -> None:
        c["liked"] = new_liked
        if new_liked:
            c["disliked"] = False

    await store.upsert_clip(clip["job_id"], clip_id, _toggle)
    return {**clip, "liked": new_liked, "disliked": False if new_liked else clip.get("disliked", False)}


@router.patch("/{clip_id}/dislike")
async def dislike_clip(clip_id: str, request: Request) -> dict[str, Any]:
    store = request.app.state.job_store
    clip = await store.get_clip(clip_id, include_retired=True)
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")
    new_disliked = not clip.get("disliked", False)

    async def _toggle(c: dict[str, Any]) -> None:
        c["disliked"] = new_disliked
        if new_disliked:
            c["liked"] = False

    await store.upsert_clip(clip["job_id"], clip_id, _toggle)
    return {**clip, "disliked": new_disliked, "liked": False if new_disliked else clip.get("liked", False)}


class RerenderRequest(BaseModel):
    reframe_provider: str = "letterbox"
    # False re-renders the video only: the clip keeps its title, summary,
    # hashtags and AI hook text instead of having them regenerated.
    regenerate_copy: bool = True


@router.post("/{clip_id}/rerender", status_code=202)
async def rerender_clip(
    clip_id: str, req: RerenderRequest, request: Request
) -> dict[str, str]:
    """Queue a re-render of one clip of a completed job, in place.

    Raises:
        HTTPException: 404 if the clip is unknown or retired; 409 if its job
            is missing or not completed, or the job's source video is gone.
    """
    store = request.app.state.job_store
    clip = await store.get_clip(clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")
    try:
        job = await store.get(clip["job_id"])
    except JobNotFoundError:
        raise HTTPException(
            status_code=409, detail="the clip's job no longer exists"
        ) from None
    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"job is {job.status}; only a completed job's clips can be re-rendered",
        )
    # Jobs created before jobs.video_path existed have no recorded source.
    if not job.video_path or not Path(job.video_path).is_file():
        raise HTTPException(status_code=409, detail="source video not retained")

    payload = {
        "rerender_clip_id": clip_id,
        "url": job.url,
        "download_path": job.download_path,
        "caption_format": job.caption_format,
        "target_aspect_ratio": job.target_aspect_ratio,
        "language": job.language,
        "pipeline_options": job.pipeline_options.model_dump(),
        # Passed through for API compatibility; reframe is unwired (task T012).
        "reframe_provider": req.reframe_provider,
        "regenerate_copy": req.regenerate_copy,
    }
    if hasattr(request.app.state, "job_queue"):
        await request.app.state.job_queue.put((job.job_id, payload))
    else:
        await request.app.state.event_bus.publish(
            Event(type=EventType.VIDEO_REQUESTED, job_id=job.job_id, payload=payload)
        )
    return {"status": "queued", "clip_id": clip_id}
