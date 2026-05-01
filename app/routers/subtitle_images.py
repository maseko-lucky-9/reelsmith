from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.subtitle_image_service import render_to_path

router = APIRouter(prefix="/subtitle-images", tags=["subtitle-images"])


class SubtitleImageRequest(BaseModel):
    text: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    font_size: int = Field(default=50, gt=0)
    output_path: str | None = None


class SubtitleImageResponse(BaseModel):
    image_path: str


@router.post("", response_model=SubtitleImageResponse)
def post_subtitle_image(req: SubtitleImageRequest) -> SubtitleImageResponse:
    if req.output_path is None:
        tmp_dir = Path(tempfile.gettempdir()) / "ytvideo-subtitles"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        path = tmp_dir / f"subtitle-{abs(hash(req.text))}.png"
    else:
        path = Path(req.output_path)
    try:
        render_to_path(req.text, (req.width, req.height), str(path), font_size=req.font_size)
    except Exception as e:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(e)) from e
    return SubtitleImageResponse(image_path=str(path))
