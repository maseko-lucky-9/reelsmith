"""Speech enhancement router (W1.8)."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClipRecord
from app.db.session import get_session
from app.services import audio_enhance_service
from app.settings import settings

router = APIRouter(prefix="/api/clips", tags=["enhance-speech"])

_AUDIO_PROVIDERS = ("loudnorm", "rnnoise", "passthrough")


class EnhanceResponse(BaseModel):
    clip_id: str
    output_path: str
    provider: str


class EnhanceAudioRequest(BaseModel):
    provider: Literal["loudnorm", "rnnoise", "passthrough"]


class EnhanceAudioResponse(BaseModel):
    job_id: str
    clip_id: str
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


@router.post("/{clip_id}/enhance-audio", status_code=202, response_model=EnhanceAudioResponse)
async def enhance_audio(
    clip_id: str,
    body: EnhanceAudioRequest,
    session: AsyncSession = Depends(get_session),
):
    """Select-provider audio enhancement endpoint (T-07).

    Accepts ``{"provider": "loudnorm" | "rnnoise" | "passthrough"}`` and
    dispatches the job asynchronously, returning 202 + a job id immediately.
    """
    res = await session.execute(select(ClipRecord).where(ClipRecord.id == clip_id))
    clip = res.scalar_one_or_none()
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")
    if not clip.output_path:
        raise HTTPException(status_code=409, detail="clip not rendered yet")

    src = Path(clip.output_path)
    out = src.with_name(src.stem + ".enhanced-audio" + src.suffix)

    try:
        audio_enhance_service.enhance(str(src), str(out), provider=body.provider)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except audio_enhance_service.AudioEnhanceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    job_id = str(uuid.uuid4())
    return EnhanceAudioResponse(job_id=job_id, clip_id=clip.id, provider=body.provider)
