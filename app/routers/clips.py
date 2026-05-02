from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.bus.job_store import JobNotFoundError

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
    clip = await store.get_clip(clip_id)
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
    clip = await store.get_clip(clip_id)
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


@router.post("/{clip_id}/rerender", status_code=202)
async def rerender_clip(clip_id: str, req: RerenderRequest, request: Request):
    clips = await request.app.state.job_store.list_clips()
    clip = next((c for c in clips if c.get("clip_id") == clip_id), None)
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")
    # Enqueue a re-render job via the job queue with the reframe setting in extra payload.
    if hasattr(request.app.state, "job_queue"):
        await request.app.state.job_queue.put((
            clip["job_id"],
            {
                "url": "",
                "download_path": "/tmp/yt",
                "caption_format": "srt",
                "target_aspect_ratio": 9 / 16,
                "rerender_clip_id": clip_id,
                "reframe_provider": req.reframe_provider,
            },
        ))
    return {"status": "queued", "clip_id": clip_id}
