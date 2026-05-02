"""Direct MP4 upload endpoint — stores file and enqueues job."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse

from app.domain.ids import new_job_id
from app.domain.models import JobState
from app.settings import settings

router = APIRouter(prefix="/uploads", tags=["uploads"])

_ALLOWED_MIME = {"video/mp4", "video/quicktime", "video/x-m4v"}
_UPLOAD_DIR = Path("/tmp/yt/uploads")


@router.post("")
async def upload_video(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    content_type = file.content_type or ""
    if content_type not in _ALLOWED_MIME:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type '{content_type}'. Allowed: {sorted(_ALLOWED_MIME)}",
        )

    max_bytes = settings.max_upload_mb * 1024 * 1024
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    upload_id = str(uuid.uuid4())
    dest = _UPLOAD_DIR / f"{upload_id}.mp4"

    total = 0
    with open(dest, "wb") as f:
        while chunk := await file.read(65536):
            total += len(chunk)
            if total > max_bytes:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds maximum size of {settings.max_upload_mb} MB",
                )
            f.write(chunk)

    job_id = new_job_id()
    state = JobState(
        job_id=job_id,
        url=f"upload://{dest}",
        download_path=str(_UPLOAD_DIR),
    )
    await request.app.state.job_store.create(state)

    if hasattr(request.app.state, "job_queue"):
        await request.app.state.job_queue.put((
            job_id,
            {
                "url": f"upload://{dest}",
                "download_path": str(_UPLOAD_DIR),
                "caption_format": "srt",
                "target_aspect_ratio": 9 / 16,
                "upload_path": str(dest),
            },
        ))

    return JSONResponse(
        {"job_id": job_id, "upload_path": str(dest), "status": "queued"},
        status_code=202,
    )
