"""YouTube source search and the clean-footage check (spec 015).

``GET /sources/youtube/search`` lists videos for a query (yt-dlp's
``ytsearchN:`` through the killable child runner, no API key).
``GET /sources/youtube/{video_id}/clean`` says whether a video carries
burned-in captions (``app/services/source_check.py``).

The query and the id reach a subprocess argv, so both are validated here and
the argv is a list, never a shell string; the search term also sits behind a
``--`` and a ``ytsearchN:`` prefix, so it can neither be read as a yt-dlp
option nor start a second argument.
"""

from __future__ import annotations

import json
import logging
import sys
import unicodedata
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

from app.services import source_check
from app.services.yt_dlp_metadata import run_bounded
from app.settings import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sources", tags=["sources"])

QUERY_MAX_CHARS = 200
DEFAULT_LIMIT = 12
MAX_LIMIT = 50
_TITLE_MAX_CHARS = 300
_STDERR_TAIL_CHARS = 300


class YoutubeSearchItem(BaseModel):
    id: str
    title: str
    channel: str
    duration_seconds: int | None
    thumbnail_url: str
    url: str


class YoutubeSearchResponse(BaseModel):
    items: list[YoutubeSearchItem]


class CleanCheckResponse(BaseModel):
    clean: bool | None
    confidence: float
    reason: str
    frames_checked: int


def _validated_query(q: str) -> str:
    """``q`` without surrounding blanks; 400 when it cannot be a plain search term."""
    if any(unicodedata.category(ch).startswith("C") for ch in q):
        raise HTTPException(status_code=400, detail="q must not contain control characters")
    term = " ".join(q.split())
    if not term:
        raise HTTPException(status_code=400, detail="q must not be empty")
    if term.startswith("-"):
        raise HTTPException(status_code=400, detail="q must not start with '-'")
    return term


def _as_text(value: object, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _item(entry: object) -> YoutubeSearchItem | None:
    """One search result, or ``None`` for an entry that is not a video."""
    if not isinstance(entry, dict):
        return None
    video_id = entry.get("id")
    if not isinstance(video_id, str) or not source_check.VIDEO_ID_RE.fullmatch(video_id):
        return None
    duration = entry.get("duration")
    channel = entry.get("channel") or entry.get("uploader")
    return YoutubeSearchItem(
        id=video_id,
        title=_as_text(entry.get("title"), _TITLE_MAX_CHARS),
        channel=_as_text(channel, _TITLE_MAX_CHARS),
        duration_seconds=int(duration) if isinstance(duration, int | float) and duration >= 0 else None,
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        url=f"https://www.youtube.com/watch?v={video_id}",
    )


@router.get("/youtube/search", response_model=YoutubeSearchResponse)
async def search_youtube(
    q: Annotated[str, Query(min_length=1, max_length=QUERY_MAX_CHARS)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> YoutubeSearchResponse:
    term = _validated_query(q)
    argv = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--flat-playlist",
        "--no-warnings",
        "-J",
        "--",
        f"ytsearch{limit}:{term}",
    ]
    try:
        returncode, stdout, stderr = await run_bounded(argv, timeout=settings.youtube_search_timeout_seconds)
    except TimeoutError:
        raise HTTPException(status_code=504, detail="YouTube search timed out") from None
    if returncode != 0:
        log.info(
            "youtube search failed  rc=%s  stderr=%s",
            returncode,
            stderr.decode("utf-8", "replace")[-_STDERR_TAIL_CHARS:],
        )
        raise HTTPException(status_code=502, detail="YouTube search failed")
    try:
        data: Any = json.loads(stdout)
    except ValueError:
        raise HTTPException(status_code=502, detail="YouTube search returned unreadable data") from None
    entries = data.get("entries") if isinstance(data, dict) else None
    items = [item for item in (_item(e) for e in entries or []) if item is not None]
    return YoutubeSearchResponse(items=items[:limit])


# ``:path`` lets a hostile id such as ``../x`` reach the check below and get its 400.
@router.get("/youtube/{video_id:path}/clean", response_model=CleanCheckResponse)
async def check_youtube_clean(video_id: Annotated[str, Path()]) -> CleanCheckResponse:
    # The id goes into a yt-dlp argv: nothing but a real id gets past this.
    if not source_check.VIDEO_ID_RE.fullmatch(video_id):
        raise HTTPException(status_code=400, detail="video_id must be an 11-character YouTube id")
    try:
        result = await source_check.check_clean(video_id)
    except source_check.CheckBusyError:
        raise HTTPException(status_code=429, detail="too many checks are running; try again shortly") from None
    return CleanCheckResponse(
        clean=result.clean,
        confidence=result.confidence,
        reason=result.reason,
        frames_checked=result.frames_checked,
    )
