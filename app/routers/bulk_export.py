"""Bulk export router (W3.7).

Streams a ZIP containing each clip's mp4, thumbnail, and a manifest
CSV. Limited by ``YTVIDEO_BULK_EXPORT_MAX_CLIPS`` to keep responses
predictable.
"""
from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClipRecord
from app.db.session import get_session
from app.settings import settings

router = APIRouter(prefix="/api/clips", tags=["bulk-export"])


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


def _zip_bytes(clips: list[ClipRecord]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.csv", _build_manifest(clips))
        for c in clips:
            for kind, path in (("mp4", c.output_path), ("jpg", c.thumbnail_path)):
                if not path:
                    continue
                p = Path(path)
                if not p.is_file():
                    continue
                zf.write(p, arcname=f"clips/{c.id}.{kind}")
    return out.getvalue()


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

    body = _zip_bytes(list(rows))
    return StreamingResponse(
        iter([body]),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="reelsmith-bulk-export.zip"',
            "Content-Length": str(len(body)),
        },
    )
