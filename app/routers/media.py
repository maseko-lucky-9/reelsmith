"""Media streaming endpoints for clip video and thumbnail assets."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.bus.job_store import JobNotFoundError

router = APIRouter(prefix="/clips", tags=["media"])

_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


def _resolve_clip_path(clips: list[dict[str, Any]], clip_id: str, field: str) -> Path:
    """Look up a clip's file path from the store result; never accept client-supplied paths."""
    for clip in clips:
        if clip.get("clip_id") == clip_id:
            raw = clip.get(field)
            if not raw:
                raise HTTPException(status_code=404, detail=f"{field} not available")
            p = Path(raw).resolve()
            # Guard against path traversal: must be an absolute path on the server
            if not p.is_file():
                raise HTTPException(status_code=404, detail="file not found")
            return p
    raise HTTPException(status_code=404, detail="clip not found")


@router.get("/{clip_id}/video")
async def stream_video(clip_id: str, request: Request):
    clips = await request.app.state.job_store.list_clips()
    path = _resolve_clip_path(clips, clip_id, "output_path")

    file_size = path.stat().st_size
    range_header = request.headers.get("range")

    filename = path.name

    if range_header:
        m = _RANGE_RE.match(range_header)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else file_size - 1
            end = min(end, file_size - 1)
            chunk_size = end - start + 1

            def _iter():
                with open(path, "rb") as f:
                    f.seek(start)
                    remaining = chunk_size
                    while remaining > 0:
                        data = f.read(min(65536, remaining))
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            return StreamingResponse(
                _iter(),
                status_code=206,
                media_type="video/mp4",
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(chunk_size),
                    "Content-Disposition": f'attachment; filename="{filename}"',
                },
            )

    return FileResponse(
        str(path),
        media_type="video/mp4",
        filename=filename,
        headers={"Accept-Ranges": "bytes"},
    )


@router.get("/{clip_id}/thumbnail")
async def get_thumbnail(clip_id: str, request: Request):
    clips = await request.app.state.job_store.list_clips()
    path = _resolve_clip_path(clips, clip_id, "thumbnail_path")
    return FileResponse(str(path), media_type="image/jpeg")
