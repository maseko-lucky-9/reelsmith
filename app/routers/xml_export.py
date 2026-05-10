"""XML export router (W1.6)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClipRecord
from app.db.session import get_session
from app.services.xml_export_service import render

router = APIRouter(prefix="/api/clips", tags=["xml-export"])

_FORMATS = {"premiere", "davinci"}


@router.get("/{clip_id}/export.xml")
async def export_clip_xml(
    clip_id: str,
    format: str = "premiere",
    session: AsyncSession = Depends(get_session),
):
    if format not in _FORMATS:
        raise HTTPException(
            status_code=422, detail=f"format must be one of {sorted(_FORMATS)}"
        )
    res = await session.execute(select(ClipRecord).where(ClipRecord.id == clip_id))
    clip = res.scalar_one_or_none()
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")
    if not clip.output_path:
        raise HTTPException(status_code=409, detail="clip not rendered yet")

    fmt = "premiere_fcp7" if format == "premiere" else "davinci_fcpxml"
    out = render(clip, fmt)  # type: ignore[arg-type]
    return Response(
        content=out.body,
        media_type=out.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{out.filename}"'
        },
    )
