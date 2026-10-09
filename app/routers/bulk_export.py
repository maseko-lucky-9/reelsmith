"""Bulk export router (W3.7).

Streams a ZIP containing each clip's mp4, thumbnail, and a manifest
CSV. Limited by ``YTVIDEO_BULK_EXPORT_MAX_CLIPS`` to keep responses
predictable.

The zip is ``ZIP_STORED`` (mp4/jpg are already compressed) and is written to
an anonymous temp file in a worker thread, so neither memory nor the event
loop scales with the export size. The temp file has no name on disk (POSIX
``TemporaryFile`` unlinks it at creation): only the open handle exists, and it
is closed when the stream ends, fails or the client disconnects, so nothing
can be left behind.
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
import tempfile
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClipRecord
from app.db.session import get_session
from app.settings import settings

router = APIRouter(prefix="/api/clips", tags=["bulk-export"])

_STREAM_CHUNK_BYTES = 1024 * 1024


def _build_manifest(clips: list[ClipRecord]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "clip_id", "title", "summary", "start", "end", "output_path",
        "thumbnail_path", "virality_score", "hashtags",
    ])
    for c in clips:
        w.writerow([
            c.id, c.title or "", c.summary or "", c.start, c.end,
            c.output_path or "", c.thumbnail_path or "",
            c.virality_score or 0,
            ",".join(c.hashtags or []),
        ])
    return buf.getvalue().encode("utf-8")


def _write_zip(clips: list[ClipRecord], dest: BinaryIO) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("manifest.csv", _build_manifest(clips))
        for c in clips:
            for kind, path in (("mp4", c.output_path), ("jpg", c.thumbnail_path)):
                if not path:
                    continue
                p = Path(path)
                if not p.is_file():
                    continue
                zf.write(p, arcname=f"clips/{c.id}.{kind}")


def _build_zip_file(clips: list[ClipRecord]) -> tuple[BinaryIO, int]:
    """Write the export zip to an unlinked temp file.

    Returns the open handle, rewound, and its size. The caller owns the handle.
    """
    handle = tempfile.TemporaryFile()
    try:
        _write_zip(clips, handle)
        size = os.fstat(handle.fileno()).st_size
        handle.seek(0)
    except BaseException:
        handle.close()
        raise
    return handle, size


async def _stream_and_close(handle: BinaryIO) -> AsyncIterator[bytes]:
    """Yield ``handle`` in chunks (reads off the loop); close it when done."""
    try:
        while chunk := await asyncio.to_thread(handle.read, _STREAM_CHUNK_BYTES):
            yield chunk
    finally:
        handle.close()


class _TempFileResponse(StreamingResponse):
    """Streams an open temp file and closes it however the response ends.

    On a client disconnect Starlette cancels the send loop while the body
    generator sits at ``yield`` and never closes it, so the generator's own
    ``finally`` would only run when it is garbage-collected; closing here
    makes the release deterministic.
    """

    def __init__(self, handle: BinaryIO, **kwargs: Any) -> None:
        super().__init__(_stream_and_close(handle), **kwargs)
        self._handle = handle

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._handle.close()


@router.get("/bulk-export.zip")
async def bulk_export(
    ids: list[str] = Query(default_factory=list, description="clip ids"),
    session: AsyncSession = Depends(get_session),
):
    if not ids:
        raise HTTPException(status_code=422, detail="no clip ids")
    max_clips = getattr(settings, "bulk_export_max_clips", 200)
    if len(ids) > max_clips:
        raise HTTPException(
            status_code=422,
            detail=f"too many clips (max {max_clips})",
        )

    rows = (
        await session.execute(
            select(ClipRecord).where(ClipRecord.id.in_(ids))
        )
    ).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="no clips matched")

    handle, size = await asyncio.to_thread(_build_zip_file, list(rows))
    return _TempFileResponse(
        handle,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="reelsmith-bulk-export.zip"',
            "Content-Length": str(size),
        },
    )
