from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.render_service import render_clip

router = APIRouter(prefix="/renders", tags=["renders"])


class RenderRequest(BaseModel):
    video_path: str
    output_path: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    captions_path: str | None = None
    target_aspect_ratio: float = 9 / 16


class RenderResponse(BaseModel):
    output_path: str


@router.post("", response_model=RenderResponse)
def post_render(req: RenderRequest) -> RenderResponse:
    if req.end <= req.start:
        raise HTTPException(status_code=422, detail="end must be greater than start")
    if not Path(req.video_path).is_file():
        raise HTTPException(status_code=404, detail=f"video not found: {req.video_path}")
    if req.captions_path and not Path(req.captions_path).is_file():
        raise HTTPException(status_code=404, detail=f"captions not found: {req.captions_path}")
    out = render_clip(
        video_path=req.video_path,
        output_path=req.output_path,
        start=req.start,
        end=req.end,
        captions_path=req.captions_path,
        target_aspect_ratio=req.target_aspect_ratio,
    )
    return RenderResponse(output_path=out)
