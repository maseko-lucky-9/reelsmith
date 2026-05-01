from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.caption_service import (
    captions_to_dicts,
    captions_to_text,
    generate_captions,
)

router = APIRouter(prefix="/captions", tags=["captions"])


class CaptionsRequest(BaseModel):
    text: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    format: str = "srt"


class CaptionItem(BaseModel):
    index: int
    start: float
    end: float
    text: str


class CaptionsResponse(BaseModel):
    format: str
    body: str
    parsed: list[CaptionItem]


@router.post("", response_model=CaptionsResponse)
def post_captions(req: CaptionsRequest) -> CaptionsResponse:
    if req.end <= req.start:
        raise HTTPException(status_code=422, detail="end must be greater than start")
    if req.format not in ("srt", "vtt"):
        raise HTTPException(status_code=422, detail="format must be 'srt' or 'vtt'")
    captions = generate_captions(req.text, req.start, req.end, format=req.format)
    return CaptionsResponse(
        format=req.format,
        body=captions_to_text(captions, req.format),
        parsed=[CaptionItem(**d) for d in captions_to_dicts(captions, req.format)],
    )
