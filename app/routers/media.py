"""Media streaming endpoints for clip video and thumbnail assets, and a job's
source video."""
from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from app.bus.job_store import JobNotFoundError
from app.services import retention
from app.workers import orchestrator

router = APIRouter(prefix="/clips", tags=["media"])
jobs_router = APIRouter(prefix="/jobs", tags=["media"])

# One range only: ``bytes=<start>-<end?>``. Suffix ranges (``bytes=-500``),
# several ranges and other units do not match and get the whole file.
_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")
_CHUNK = 65536

_SOURCE_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
}


def _iter_file(path: Path, start: int, length: int) -> Iterator[bytes]:
    with open(path, "rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            data = f.read(min(_CHUNK, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


def range_response(
    path: Path,
    range_header: str | None,
    *,
    media_type: str,
    disposition: str,
) -> Response:
    """Answer a request for ``path`` honouring one ``Range``.

    No ``Range``, or one that does not match ``bytes=<start>-<end?>``: 200
    with the whole file. ``start < size`` and ``start <= end``: 206 with
    those bytes (``end`` clamped to the last byte; a missing ``end`` is the
    last byte). Otherwise 416 with ``Content-Range: bytes */<size>``.

    Args:
        path: The file to send (already checked by the caller).
        range_header: The request's ``Range`` header, if any.
        media_type: The ``Content-Type``.
        disposition: ``inline`` or ``attachment``; the file name is added.
    """
    size = path.stat().st_size
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'{disposition}; filename="{path.name}"',
    }
    m = _RANGE_RE.fullmatch(range_header.strip()) if range_header else None
    if m is None:
        return StreamingResponse(
            _iter_file(path, 0, size),
            media_type=media_type,
            headers={**headers, "Content-Length": str(size)},
        )
    start = int(m.group(1))
    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    if start >= size or start > end:
        return JSONResponse(
            {"detail": "range not satisfiable"},
            status_code=416,
            headers={"Content-Range": f"bytes */{size}"},
        )
    length = end - start + 1
    return StreamingResponse(
        _iter_file(path, start, length),
        status_code=206,
        media_type=media_type,
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(length),
        },
    )


async def _resolve_clip_path(request: Request, clip_id: str, field: str) -> Path:
    """Look up a clip's file path in the store; never accept client-supplied paths.

    Retired clips 404, as they are hidden from ``list_clips``.
    """
    clip: dict[str, Any] | None = await request.app.state.job_store.get_clip(clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")
    raw = clip.get(field)
    if not raw:
        raise HTTPException(status_code=404, detail=f"{field} not available")
    p = Path(raw).resolve()
    # Guard against path traversal: must be an absolute path on the server
    if not p.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return p


@router.get("/{clip_id}/video")
async def stream_video(clip_id: str, request: Request):
    path = await _resolve_clip_path(request, clip_id, "output_path")
    return range_response(
        path,
        request.headers.get("range"),
        media_type="video/mp4",
        disposition="attachment",
    )


@jobs_router.get("/{job_id}/source")
async def stream_job_source(job_id: str, request: Request):
    """The job's source video, read-only, for the editor's clean preview.

    Raises:
        HTTPException: 404 for an unknown job, and ``source video not
            retained`` for every path failure (empty, unsafe, outside the
            managed folders, symlinked out, not a regular file), never
            echoing the path; 409 while the job is being deleted.
    """
    try:
        job = await request.app.state.job_store.get(job_id)
    except JobNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}") from e
    if orchestrator.deleting(job_id):
        raise HTTPException(status_code=409, detail="the job is being deleted")
    not_retained = HTTPException(status_code=404, detail="source video not retained")
    raw = job.video_path
    if not raw or not retention.is_managed_file(raw):
        raise not_retained
    try:
        # is_managed_file resolved symlinks; serve the resolved file.
        path = Path(raw).resolve(strict=True)
        if not path.is_file():
            raise not_retained
    except (OSError, RuntimeError, ValueError):
        raise not_retained from None
    return range_response(
        path,
        request.headers.get("range"),
        media_type=_SOURCE_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        disposition="inline",
    )


@router.get("/{clip_id}/thumbnail")
async def get_thumbnail(clip_id: str, request: Request):
    path = await _resolve_clip_path(request, clip_id, "thumbnail_path")
    return FileResponse(str(path), media_type="image/jpeg")
