"""AI hook generation router (W1.7)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClipRecord
from app.db.session import get_session
from app.services.ai_hook_service import generate_hook

router = APIRouter(prefix="/api/clips", tags=["ai-hook"])


class HookResponse(BaseModel):
    clip_id: str
    hook: str


@router.post("/{clip_id}/ai-hook", response_model=HookResponse)
async def create_hook(
    clip_id: str, session: AsyncSession = Depends(get_session)
):
    res = await session.execute(select(ClipRecord).where(ClipRecord.id == clip_id))
    clip = res.scalar_one_or_none()
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")

    transcript_text = ""
    if isinstance(clip.transcript, dict):
        # Best-effort flatten of common shapes (faster-whisper segments).
        if "text" in clip.transcript and isinstance(clip.transcript["text"], str):
            transcript_text = clip.transcript["text"]
        elif "segments" in clip.transcript and isinstance(
            clip.transcript["segments"], list
        ):
            transcript_text = " ".join(
                s.get("text", "") for s in clip.transcript["segments"]
            )
    transcript_text = (transcript_text or clip.summary or clip.title or "").strip()

    hook = generate_hook(transcript_text)
    clip.ai_hook_text = hook or None
    await session.commit()
    return HookResponse(clip_id=clip.id, hook=hook)
