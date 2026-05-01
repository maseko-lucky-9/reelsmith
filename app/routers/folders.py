from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.folder_service import create_video_subfolder

router = APIRouter(prefix="/folders", tags=["folders"])


class FolderRequest(BaseModel):
    download_path: str
    url: str


class FolderResponse(BaseModel):
    destination_folder: str
    clips_folder: str


@router.post("", response_model=FolderResponse)
def post_folder(req: FolderRequest) -> FolderResponse:
    Path(req.download_path).mkdir(parents=True, exist_ok=True)
    try:
        destination, clips = create_video_subfolder(req.download_path, req.url)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"folder creation failed: {e}") from e
    return FolderResponse(destination_folder=destination, clips_folder=clips)
