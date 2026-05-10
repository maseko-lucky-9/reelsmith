"""Speech enhancement router (W1.8)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClipRecord
from app.db.session import get_session
from app.services import audio_enhance_service
from app.settings import settings

router = APIRouter(prefix="/api/clips", tags=["enhance-speech"])


class EnhanceResponse(BaseModel):
    clip_id: str
    output_path: str
    provider: str


@router.post("/{clip_id}/enhance-speech", response_model=EnhanceResponse)
async def enhance_speech(
    clip_id: str, session: AsyncSession = Depends(get_session)
):
    res = await session.execute(select(ClipRecord).where(ClipRecord.id == clip_id))
    clip = res.scalar_one_or_none()
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")
    if not clip.output_path:
        raise HTTPException(status_code=409, detail="clip not rendered yet")

    src = Path(clip.output_path)
    out = src.with_name(src.stem + ".enhanced" + src.suffix)
    provider = getattr(settings, "audio_enhance_provider", "loudnorm")

    try:
        audio_enhance_service.enhance(str(src), str(out), provider=provider)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except audio_enhance_service.AudioEnhanceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    clip.output_path = str(out)
    await session.commit()
    return EnhanceResponse(clip_id=clip.id, output_path=str(out), provider=provider)
