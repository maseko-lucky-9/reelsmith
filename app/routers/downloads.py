from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.domain.models import Chapter
from app.services.download_service import download_video, extract_chapters

router = APIRouter(prefix="/downloads", tags=["downloads"])


class DownloadRequest(BaseModel):
    url: str
    destination_folder: str


class DownloadResponse(BaseModel):
    video_path: str
    title: str
    duration: float | None
    chapters: list[Chapter]


@router.post("", response_model=DownloadResponse)
def post_download(req: DownloadRequest) -> DownloadResponse:
    video_path, info = download_video(req.url, req.destination_folder)
    if not video_path or info is None:
        raise HTTPException(status_code=502, detail="download failed")
    chapters = extract_chapters(info)
    return DownloadResponse(
        video_path=video_path,
        title=info.get("title", ""),
        duration=info.get("duration"),
        chapters=[Chapter(**c) for c in chapters],
    )
