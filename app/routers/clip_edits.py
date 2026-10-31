"""CRUD endpoints for ``ClipEdit`` (W1.2 — inline editor timeline state).

REST contract:

* ``GET    /clips/{clip_id}/edit``    → edit state; with none saved, a
  default timeline built from the clip's words (``version`` 0,
  ``is_default``), or 404 when the clip has no rendered file
* ``PUT    /clips/{clip_id}/edit``    → upsert; bumps ``version``
* ``DELETE /clips/{clip_id}/edit``    → discard edits (clip falls
  back to the main pipeline render on next view)

Concurrency: optimistic; clients send ``version`` in the body. A
mismatch returns 409. The render endpoint lives in W1.2-render
(separate PR) and consumes ``timeline`` to produce the editor proxy.

ADR-003 §Wave 1 · timeline schema:

```jsonc
{
  "tracks": [
    { "kind": "video",        "items": [ { "start": 0.0, "end": 12.4, "src": "main", "trim_start": 0.0 } ] },
    { "kind": "caption",      "items": [ { "start": 0.0, "end": 12.4, "style": "default" } ] },
    { "kind": "text-overlay", "items": [ { "start": 1.0, "end": 4.0,  "text": "🔥 hook", "x": 0.5, "y": 0.1 } ] }
  ]
}
```

Validation in this PR is structural only (tracks list of dicts with a
``kind`` discriminator). Per-item schema is enforced in W1.12 by
``timeline_render_service``.

Words (spec 016, T126): ``GET`` also returns ``words``, the clip's word
timings on the clip's clock (``<job folder>/.clip_words/<clip stem>.words.json``,
written by the pipeline; ``clip_words.clip_words_path``). A clip rendered
before that has no sidecar: the first ``GET`` transcribes the clip file and
caches it (``clip_words.backfill_clip_words``). That backfill never runs
without the clip file, for a file outside the download folder (an export
copy), for a clip whose sidecar path is refused, or while the clip's job is
busy or being deleted; a failed or timed-out one answers with no words,
never an error.
Default caption items carry their words' timings (``words``).

Timeline schema v2 (spec 016, T127; ``timeline_render_service``): a timeline
with ``"schema_version": 2`` holds video segments in source seconds, captions
with words, text overlays, voice-over ``audio`` items, ``caption_style`` and
``mute_source``. ``PUT`` validates every timeline (v1 or v2) before storing it
and answers 422 with the reason; a v1 timeline is stored as sent. The server
supplies the bounds: the source video's range (its probed duration, else the
job's recorded duration, else the clip's own window) and the clip's folder,
the only place a voice-over ``src`` name may resolve.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClipEdit, ClipRecord, JobRecord
from app.db.session import get_session
from app.services import clip_words, ffmpeg_tools
from app.services.retention import is_managed_file
from app.services.timeline_render_service import (
    TimelineError,
    build_render_plan,
    validate_timeline,
)
from app.services.transcription_service import WordTiming
from app.settings import settings
from app.workers import orchestrator

log = logging.getLogger(__name__)

router = APIRouter(prefix="/clips", tags=["clip-edits"])


_VALID_TRACK_KINDS: set[str] = {"video", "caption", "text-overlay", "audio"}

# Top-level timeline keys stored only when the client sent them, so a v1
# timeline is stored exactly as sent.
_OPTIONAL_TIMELINE_KEYS = ("schema_version", "caption_style", "mute_source")


class TimelineTrack(BaseModel):
    kind: Literal["video", "caption", "text-overlay", "audio"]
    items: list[dict[str, Any]] = Field(default_factory=list)


class TimelinePayload(BaseModel):
    tracks: list[TimelineTrack] = Field(default_factory=list)
    # Absent: v1. Kinds, item shapes and bounds per version are checked by
    # timeline_render_service.validate_timeline.
    schema_version: Literal[1, 2] | None = None
    caption_style: str | None = None
    mute_source: StrictBool | None = None

    def stored(self) -> dict[str, Any]:
        data = self.model_dump()
        for key in _OPTIONAL_TIMELINE_KEYS:
            if key not in self.model_fields_set:
                data.pop(key)
        return data


class ClipEditUpsert(BaseModel):
    timeline: TimelinePayload
    # Optional optimistic-concurrency token. When omitted, the server
    # treats it as a force-overwrite (first save).
    version: int | None = None


def _to_dict(edit: ClipEdit) -> dict[str, Any]:
    return {
        "clip_id": edit.clip_id,
        "timeline": edit.timeline,
        "version": edit.version,
        "created_at": edit.created_at.isoformat(),
        "updated_at": edit.updated_at.isoformat(),
    }


async def _load_clip(session: AsyncSession, clip_id: str) -> ClipRecord:
    result = await session.execute(select(ClipRecord).where(ClipRecord.id == clip_id))
    clip = result.scalar_one_or_none()
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")
    return clip


async def _load_edit(session: AsyncSession, clip_id: str) -> ClipEdit | None:
    result = await session.execute(select(ClipEdit).where(ClipEdit.clip_id == clip_id))
    return result.scalar_one_or_none()


def _clip_file(clip: ClipRecord) -> str | None:
    """The clip's rendered file when it exists (an absolute path to a regular
    file), else None."""
    raw = clip.output_path
    if not raw or "\x00" in raw or not os.path.isabs(raw):
        return None
    return raw if Path(raw).is_file() else None


async def _clip_words(
    session: AsyncSession, clip: ClipRecord, clip_file: str
) -> list[WordTiming]:
    """The clip's words: its sidecar, else a backfill (module docstring)."""
    duration = float(clip.end or 0.0) - float(clip.start or 0.0)
    cached = await asyncio.to_thread(clip_words.read_clip_words, clip_file)
    if cached is not None:
        return cached
    job_id = clip.job_id
    if not is_managed_file(clip_file):
        log.info("clip %s: no words sidecar; its file is an export copy, not transcribed", clip.id)
        return []
    if clip_words.clip_words_path(clip_file) is None:
        log.info("clip %s: no words sidecar; its path is refused, not transcribed", clip.id)
        return []
    if orchestrator.deleting(job_id) or orchestrator.job_active(job_id):
        log.info("clip %s: no words sidecar; its job is busy, not transcribed now", clip.id)
        return []
    job = await session.get(JobRecord, job_id)
    language = job.language if job is not None else None
    # Counted as work on the job, so a job delete meanwhile answers 409.
    orchestrator.claim_work(job_id)
    try:
        words = await clip_words.backfill_clip_words(
            clip_file, duration=duration, language=language
        )
    finally:
        orchestrator.release_work(job_id)
    return words or []


def _clip_folder(clip: ClipRecord) -> Path | None:
    """The folder of the clip's rendered file: where voice-over files live."""
    clip_file = _clip_file(clip)
    return Path(clip_file).parent if clip_file else None


def _positive(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


async def _source(
    session: AsyncSession, clip: ClipRecord
) -> tuple[str | None, tuple[float, float] | None]:
    """The clip's source video (when on disk) and the source window a v2
    segment must lie in: ``[0, duration]`` of the source (probed, else the
    job's recorded duration), else the clip's own window, else unknown."""
    job = await session.get(JobRecord, clip.job_id)
    raw = job.video_path if job is not None else None
    path = raw if raw and "\x00" not in raw and os.path.isabs(raw) and Path(raw).is_file() else None
    duration: float | None = None
    if path is not None:
        try:
            duration = _positive(await asyncio.to_thread(ffmpeg_tools.duration, path))
        except Exception as exc:  # noqa: BLE001 - fall back to the recorded duration
            log.info("clip %s: source duration probe failed (%s)", clip.id, exc)
    if duration is None and job is not None and isinstance(job.state, dict):
        duration = _positive(job.state.get("duration"))
    if duration is not None:
        return path, (0.0, duration)
    start, end = float(clip.start or 0.0), float(clip.end or 0.0)
    if end > start >= 0:
        return path, (start, end)
    return path, None


@router.get("/{clip_id}/edit")
async def get_clip_edit(clip_id: str, session: AsyncSession = Depends(get_session)):
    clip = await _load_clip(session, clip_id)
    edit = await _load_edit(session, clip_id)
    clip_file = _clip_file(clip)
    if edit is None and clip_file is None:
        raise HTTPException(status_code=404, detail="no edit state for clip")
    words = await _clip_words(session, clip, clip_file) if clip_file else []
    if edit is not None:
        return {**_to_dict(edit), "words": clip_words.words_to_dicts(words)}
    duration = float(clip.end or 0.0) - float(clip.start or 0.0)
    return {
        "clip_id": clip.id,
        "timeline": clip_words.default_timeline(
            words, duration, settings.caption_words_per_segment
        ),
        "version": 0,
        "created_at": None,
        "updated_at": None,
        "is_default": True,
        "words": clip_words.words_to_dicts(words),
    }


@router.put("/{clip_id}/edit")
async def upsert_clip_edit(
    clip_id: str,
    body: ClipEditUpsert,
    session: AsyncSession = Depends(get_session),
):
    clip = await _load_clip(session, clip_id)
    edit = await _load_edit(session, clip_id)

    timeline_dict = body.timeline.stored()

    # Defensive: discriminator validity is enforced by Pydantic, but we
    # double-check for forward compat (kind allowlist may grow before the
    # frontend ships).
    for track in timeline_dict["tracks"]:
        if track["kind"] not in _VALID_TRACK_KINDS:
            raise HTTPException(
                status_code=422,
                detail=f"unknown track kind: {track['kind']!r}",
            )
    _, source_range = await _source(session, clip)
    try:
        await asyncio.to_thread(
            validate_timeline,
            timeline_dict,
            source_range=source_range,
            clip_folder=_clip_folder(clip),
        )
    except TimelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if edit is None:
        edit = ClipEdit(clip_id=clip_id, timeline=timeline_dict, version=1)
        session.add(edit)
    else:
        if body.version is not None and body.version != edit.version:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "version mismatch",
                    "server_version": edit.version,
                    "client_version": body.version,
                },
            )
        edit.timeline = timeline_dict
        edit.version = edit.version + 1

    await session.commit()
    await session.refresh(edit)
    return _to_dict(edit)


@router.get("/{clip_id}/edit/plan")
async def get_render_plan(
    clip_id: str, session: AsyncSession = Depends(get_session)
):
    """Return the deterministic render plan for the editor preview."""
    clip = await _load_clip(session, clip_id)
    edit = await _load_edit(session, clip_id)
    if edit is None:
        raise HTTPException(status_code=404, detail="no edit state for clip")
    base = clip.output_path or ""
    source_path, source_range = await _source(session, clip)
    try:
        plan = await asyncio.to_thread(
            build_render_plan,
            edit.timeline,
            base,
            source_path=source_path,
            source_range=source_range,
            clip_folder=_clip_folder(clip),
        )
    except TimelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return plan.to_dict()


@router.delete("/{clip_id}/edit", status_code=204)
async def delete_clip_edit(
    clip_id: str, session: AsyncSession = Depends(get_session)
):
    await _load_clip(session, clip_id)
    edit = await _load_edit(session, clip_id)
    if edit is None:
        return  # idempotent — no edit state to discard
    await session.delete(edit)
    await session.commit()
