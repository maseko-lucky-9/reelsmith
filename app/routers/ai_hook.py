"""AI hook generation router (W1.7).

The hook is stored on the clip (``ClipRecord.ai_hook_text``), where
``POST /clips/{id}/voiceover`` reads its default script (spec 016).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClipRecord
from app.db.session import get_session
from app.services.ai_hook_service import generate_hook

router = APIRouter(prefix="/clips", tags=["ai-hook"])


class HookResponse(BaseModel):
    clip_id: str
    hook: str


def _segments_text(segments: list[Any]) -> str:
    return " ".join(
        seg["text"] for seg in segments if isinstance(seg, dict) and isinstance(seg.get("text"), str)
    )


def _transcript_text(transcript: Any) -> str:
    """The clip's transcript as plain text. The pipeline stores it as a string
    (``orchestrator``: ``"transcript": text``) in a JSON column; older rows or
    imports may hold ``{"text": ...}``, ``{"segments": [...]}`` or a segment
    list. Anything else counts as no transcript."""
    if isinstance(transcript, str):
        return transcript
    if isinstance(transcript, list):
        return _segments_text(transcript)
    if isinstance(transcript, dict):
        if isinstance(transcript.get("text"), str):
            return transcript["text"]
        if isinstance(transcript.get("segments"), list):
            return _segments_text(transcript["segments"])
    return ""


@router.post("/{clip_id}/ai-hook", response_model=HookResponse)
async def create_hook(
    clip_id: str, session: AsyncSession = Depends(get_session)
):
    res = await session.execute(select(ClipRecord).where(ClipRecord.id == clip_id))
    clip = res.scalar_one_or_none()
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")

    transcript_text = (_transcript_text(clip.transcript) or clip.summary or clip.title or "").strip()

    hook = generate_hook(transcript_text)
    clip.ai_hook_text = hook or None
    await session.commit()
    return HookResponse(clip_id=clip.id, hook=hook)
