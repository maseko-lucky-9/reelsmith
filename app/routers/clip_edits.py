"""CRUD endpoints for ``ClipEdit`` (W1.2 — inline editor timeline state).

REST contract:

* ``GET    /api/clips/{clip_id}/edit``    → edit state or 404
* ``PUT    /api/clips/{clip_id}/edit``    → upsert; bumps ``version``
* ``DELETE /api/clips/{clip_id}/edit``    → discard edits (clip falls
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
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClipEdit, ClipRecord
from app.db.session import get_session
from app.services.timeline_render_service import (
    TimelineError,
    build_render_plan,
)

router = APIRouter(prefix="/api/clips", tags=["clip-edits"])


_VALID_TRACK_KINDS: set[str] = {"video", "caption", "text-overlay"}


class TimelineTrack(BaseModel):
    kind: Literal["video", "caption", "text-overlay"]
    items: list[dict[str, Any]] = Field(default_factory=list)


class TimelinePayload(BaseModel):
    tracks: list[TimelineTrack] = Field(default_factory=list)


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


@router.get("/{clip_id}/edit")
async def get_clip_edit(clip_id: str, session: AsyncSession = Depends(get_session)):
    await _load_clip(session, clip_id)
    edit = await _load_edit(session, clip_id)
    if edit is None:
        raise HTTPException(status_code=404, detail="no edit state for clip")
    return _to_dict(edit)


@router.put("/{clip_id}/edit")
async def upsert_clip_edit(
    clip_id: str,
    body: ClipEditUpsert,
    session: AsyncSession = Depends(get_session),
):
    await _load_clip(session, clip_id)
    edit = await _load_edit(session, clip_id)

    timeline_dict = body.timeline.model_dump()

    # Defensive: discriminator validity is enforced by Pydantic, but we
    # double-check for forward compat (kind allowlist may grow before the
    # frontend ships).
    for track in timeline_dict["tracks"]:
        if track["kind"] not in _VALID_TRACK_KINDS:
            raise HTTPException(
                status_code=422,
                detail=f"unknown track kind: {track['kind']!r}",
            )

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
    try:
        plan = build_render_plan(edit.timeline, base)
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
