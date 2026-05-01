from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.transcription_service import speech_to_text

router = APIRouter(prefix="/transcriptions", tags=["transcriptions"])


class TranscriptionRequest(BaseModel):
    audio_path: str
    language: str = "en-US"


class TranscriptionResponse(BaseModel):
    text: str


@router.post("", response_model=TranscriptionResponse)
def post_transcription(req: TranscriptionRequest) -> TranscriptionResponse:
    if not Path(req.audio_path).is_file():
        raise HTTPException(status_code=404, detail=f"audio not found: {req.audio_path}")
    text = speech_to_text(req.audio_path, language=req.language)
    return TranscriptionResponse(text=text)
