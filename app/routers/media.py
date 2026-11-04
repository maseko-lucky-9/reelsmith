"""Media streaming endpoints for clip video and thumbnail assets, and a job's
source video."""
from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote

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


def content_disposition(disposition: str, name: str) -> str:
    """An RFC 6266 / 5987 ``Content-Disposition`` value for ``name``.

    The plain ``filename`` is an ASCII fallback (``"``, ``\\``, control and
    non-ASCII characters become ``_``); ``filename*`` carries the exact name
    percent-encoded as UTF-8 (only when it differs from the fallback), so the header is always Latin-1 safe and cannot
    be broken or injected through a file name.
    """
    fallback = "".join(
        c if " " <= c < "\x7f" and c not in '"\\%' else "_" for c in name
    )
    value = f'{disposition}; filename="{fallback}"'
    return value if fallback == name else f"{value}; filename*=utf-8''{quote(name, safe='', errors='replace')}"


class _FileResponse(StreamingResponse):
    """Streams an already-open file and closes it when the response ends,
    however it ends (done, client abort, cancellation)."""

    def __init__(self, f: BinaryIO, start: int, length: int, **kwargs: Any) -> None:
        self._file = f
        super().__init__(self._iter(f, start, length), **kwargs)

    @staticmethod
    def _iter(f: BinaryIO, start: int, length: int) -> Iterator[bytes]:
        f.seek(start)
        remaining = length
        while remaining > 0:
            data = f.read(min(_CHUNK, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._file.close()


def _open_regular(path: Path, missing_detail: str) -> tuple[BinaryIO, int]:
    """Open ``path`` once (never following a final symlink where the OS can)
    and size it from the descriptor, so a file removed or swapped after the
    caller's checks is a 404, not a 500 or a wrong size."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise HTTPException(status_code=404, detail=missing_detail) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise HTTPException(status_code=404, detail=missing_detail)
        return os.fdopen(fd, "rb"), st.st_size
    except BaseException:
        os.close(fd)
        raise


def range_response(
    path: Path,
    range_header: str | None,
    *,
    media_type: str,
    disposition: str,
    missing_detail: str = "file not found",
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
        missing_detail: The 404 ``detail`` if the file cannot be opened.

    Raises:
        HTTPException: 404 when the file is gone or is not a regular file.
    """
    f, size = _open_regular(path, missing_detail)
    try:
        return _respond(f, size, path.name, range_header, media_type, disposition)
    except BaseException:
        f.close()
        raise


def _respond(
    f: BinaryIO,
    size: int,
    name: str,
    range_header: str | None,
    media_type: str,
    disposition: str,
) -> Response:
    """Build the response for the open file ``f``; the caller closes ``f`` if
    this raises."""
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": content_disposition(disposition, name),
    }
    m = _RANGE_RE.fullmatch(range_header.strip()) if range_header else None
    if m is None:
        return _FileResponse(
            f, 0, size, media_type=media_type, headers={**headers, "Content-Length": str(size)}
        )
    start = int(m.group(1))
    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    if start >= size or start > end:
        f.close()
        return JSONResponse(
            {"detail": "range not satisfiable"},
            status_code=416,
            headers={"Content-Range": f"bytes */{size}"},
        )
    length = end - start + 1
    return _FileResponse(
        f,
        start,
        length,
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
        missing_detail="source video not retained",
    )


@router.get("/{clip_id}/thumbnail")
async def get_thumbnail(clip_id: str, request: Request):
    path = await _resolve_clip_path(request, clip_id, "thumbnail_path")
    return FileResponse(str(path), media_type="image/jpeg")
