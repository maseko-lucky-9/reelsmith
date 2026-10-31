"""Voice-over for a clip (spec 016): ``POST /clips/{clip_id}/voiceover``.

Body (all optional): ``{"text": str, "voice": str, "start": float}``.

* ``text`` omitted: the clip's AI hook (``ClipRecord.ai_hook_text``) is
  spoken; 404 when the clip has none. Text is untrusted and validated by
  ``voiceover_service.clean_text`` (422).
* ``voice`` omitted: ``YTVIDEO_VOICEOVER_VOICE``; a bad name is 422.
* ``start``: where the voice-over starts on the edited reel (default 0).

The engine is ``YTVIDEO_VOICEOVER_PROVIDER`` (``voiceover_service.resolve_provider``;
"off" answers 503). The wav is written as ``voiceover_<n>.wav`` in the clip's
own folder, the name chosen by the server, and appended as an ``audio`` item
``{start, end, src, gain: 1.0}`` to the clip's saved edit, ``end`` = start +
the wav's duration, clamped to the edited reel's end. With no saved edit a
schema v2 one is created from the default timeline (the clip's window as one
source segment, its captions); a saved v1 edit is refused (409: its video
items are on the reel's clock and cannot be mapped to source seconds safely),
open and save it in the editor first.

Other answers: 404 unknown clip or no clip file; 409 a clip file outside the
download folder (an export copy), or the clip's job busy or being deleted
(the synthesis is counted as work on the job, ``orchestrator.claim_work``);
502 the engine failed. A failed request leaves no wav behind. Deleting the
audio item later does not delete the wav; deleting the job does (it lives in
the job's folder).
"""

from __future__ import annotations

import copy
import logging
import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClipEdit
from app.db.session import get_session
from app.routers.clip_edits import _clip_file, _clip_words, _load_clip, _load_edit, _source, _to_dict
from app.services import clip_words, ffmpeg_tools, voiceover_service
from app.services.retention import is_managed_file
from app.services.timeline_render_service import TimelineError, validate_timeline
from app.settings import settings
from app.workers import orchestrator

log = logging.getLogger(__name__)

router = APIRouter(prefix="/clips", tags=["voiceover"])

MAX_START_SECONDS = 3600.0


class VoiceoverRequest(BaseModel):
    text: str | None = Field(default=None, max_length=4 * voiceover_service.MAX_TEXT_CHARS)
    voice: str | None = Field(default=None, max_length=64)
    start: float | None = Field(default=None, ge=0.0, le=MAX_START_SECONDS, allow_inf_nan=False)


def _reel_end(timeline: dict[str, Any]) -> float:
    """The edited reel's length: the sum of a v2 timeline's video segments."""
    total = 0.0
    for track in timeline.get("tracks") or []:
        if isinstance(track, dict) and track.get("kind") == "video":
            for item in track.get("items") or []:
                try:
                    total += max(0.0, float(item["end"]) - float(item["start"]))
                except (KeyError, TypeError, ValueError):
                    continue
    return total


def _default_v2(words: list[Any], start: float, end: float) -> dict[str, Any]:
    """The default timeline as schema v2: the clip's window as one source
    segment, the default captions, no overlays, an empty audio track."""
    base = clip_words.default_timeline(words, end - start, settings.caption_words_per_segment)
    tracks = [t for t in base["tracks"] if t["kind"] != "video"]
    return {
        "schema_version": 2,
        "tracks": [{"kind": "video", "items": [{"start": start, "end": end, "src": "main"}]}, *tracks],
    }


def _with_audio(timeline: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(timeline)
    tracks = updated.setdefault("tracks", [])
    audio = next((t for t in tracks if isinstance(t, dict) and t.get("kind") == "audio"), None)
    if audio is None:
        audio = {"kind": "audio", "items": []}
        tracks.append(audio)
    audio.setdefault("items", []).append(item)
    return updated


def _discard(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("voiceover: could not remove %s: %s", path, exc)


@router.post("/{clip_id}/voiceover")
async def create_voiceover(
    clip_id: str,
    body: VoiceoverRequest | None = None,
    session: AsyncSession = Depends(get_session),
):
    body = body or VoiceoverRequest()
    clip = await _load_clip(session, clip_id)
    clip_file = _clip_file(clip)
    if clip_file is None:
        raise HTTPException(status_code=404, detail="clip file not found")

    raw_text = body.text if body.text is not None else clip.ai_hook_text
    if body.text is None and not (raw_text or "").strip():
        raise HTTPException(status_code=404, detail="clip has no AI hook text; send text")
    voice = body.voice if body.voice is not None else settings.voiceover_voice
    try:
        script = voiceover_service.clean_text(raw_text)
        voiceover_service.voice_model_path(voice)
        provider = voiceover_service.resolve_provider(None, voice)
    except voiceover_service.VoiceoverInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except voiceover_service.VoiceoverDisabled as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except voiceover_service.VoiceoverError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not is_managed_file(clip_file):
        raise HTTPException(
            status_code=409,
            detail="clip file is outside the download folder (an export copy); no voice-over written",
        )
    edit = await _load_edit(session, clip_id)
    if edit is not None and (edit.timeline or {}).get("schema_version") != 2:
        raise HTTPException(
            status_code=409,
            detail="the clip's saved edit is schema v1; open and save it in the editor first",
        )
    if edit is not None:
        timeline = edit.timeline
    else:
        start, end = float(clip.start or 0.0), float(clip.end or 0.0)
        if not (math.isfinite(start) and math.isfinite(end) and end > start >= 0):
            raise HTTPException(status_code=409, detail="the clip's source window is unknown")
        words = await _clip_words(session, clip, clip_file)
        timeline = _default_v2(words, start, end)
    reel_end = _reel_end(timeline)
    item_start = float(body.start or 0.0)
    if item_start >= reel_end:
        raise HTTPException(status_code=422, detail=f"start {item_start} is at or past the reel's end ({reel_end:.3f})")
    _, source_range = await _source(session, clip)

    job_id = clip.job_id
    if orchestrator.deleting(job_id):
        raise HTTPException(status_code=409, detail="the clip's job is being deleted")
    if orchestrator.job_active(job_id):
        raise HTTPException(status_code=409, detail="the clip's job is busy; try again when it is done")
    orchestrator.claim_work(job_id)
    out: Path | None = None
    done = False
    try:
        folder = Path(clip_file).parent
        out = voiceover_service.reserve_output_path(folder)
        try:
            seconds = await ffmpeg_tools.to_thread_cancellable(
                voiceover_service.synthesize_voiceover, script, voice, out, provider=provider
            )
        except voiceover_service.VoiceoverInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except voiceover_service.VoiceoverError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        item_end = min(item_start + seconds, reel_end)
        item = {"start": round(item_start, 3), "end": round(item_end, 3), "src": out.name, "gain": 1.0}
        if item["end"] <= item["start"]:
            raise HTTPException(status_code=422, detail="the voice-over does not fit before the reel's end")
        updated = _with_audio(timeline, item)
        try:
            validate_timeline(updated, source_range=source_range, clip_folder=folder)
        except TimelineError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if edit is None:
            edit = ClipEdit(clip_id=clip_id, timeline=updated, version=1)
            session.add(edit)
        else:
            edit.timeline = updated
            edit.version = edit.version + 1
        await session.commit()
        await session.refresh(edit)
        done = True
    finally:
        orchestrator.release_work(job_id)
        if not done:
            _discard(out)
    log.info("voiceover: clip %s +%s (%.2fs, %s)", clip_id, item["src"], seconds, provider)
    return {"edit": _to_dict(edit), "item": item, "provider": provider}
