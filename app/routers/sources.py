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
import math
import unicodedata
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

from app.services import source_check
from app.services.loop_local import LoopLocalSemaphore
from app.services.yt_dlp_metadata import run_bounded
from app.settings import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sources", tags=["sources"])

QUERY_MAX_CHARS = 200
DEFAULT_LIMIT = 12
MAX_LIMIT = 50
_TITLE_MAX_CHARS = 300
_STDERR_TAIL_CHARS = 300
# yt-dlp searches at once; a further one is refused (429) rather than queued behind them.
MAX_CONCURRENT_SEARCHES = 2
_SEARCH_SLOTS = LoopLocalSemaphore(MAX_CONCURRENT_SEARCHES)

# Characters a query may not contain: control, surrogate, private-use and unassigned
# characters, line and paragraph separators, and the invisible formatting characters that
# reorder or hide text. Zero-width joiners (U+200D, U+200C) stay: emoji sequences need them.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cs", "Co", "Cn", "Zl", "Zp"})
_FORBIDDEN_FORMAT_CHARS = frozenset("\u200b\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff")


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
    # Why ``clean`` is null: stream_blocked, stream_failed, frames_failed, ollama_unavailable,
    # reply_unusable, inconclusive, timeout or internal. Null when the check was made.
    cause: str | None = None


def _forbidden(ch: str) -> bool:
    return unicodedata.category(ch) in _FORBIDDEN_CATEGORIES or ch in _FORBIDDEN_FORMAT_CHARS


def _validated_query(q: str) -> str:
    """``q`` without surrounding blanks; 400 when it cannot be a plain search term."""
    if any(_forbidden(ch) for ch in q):
        raise HTTPException(status_code=400, detail="q must not contain control characters")
    term = " ".join(q.split())
    if not term:
        raise HTTPException(status_code=400, detail="q must not be empty")
    if term.startswith("-"):
        raise HTTPException(status_code=400, detail="q must not start with '-'")
    return term


def _as_text(value: object, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _seconds(value: object) -> int | None:
    """``value`` as whole seconds; ``None`` unless it is a finite, non-negative number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return int(value)


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
        duration_seconds=_seconds(duration),
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        url=f"https://www.youtube.com/watch?v={video_id}",
    )


@router.get("/youtube/search", response_model=YoutubeSearchResponse)
async def search_youtube(
    q: Annotated[str, Query(min_length=1, max_length=QUERY_MAX_CHARS)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> YoutubeSearchResponse:
    term = _validated_query(q)
    argv = source_check.yt_dlp_argv("--flat-playlist", "--no-warnings", "-J", "--", f"ytsearch{limit}:{term}")
    slot = _SEARCH_SLOTS.get()
    if slot.locked():
        raise HTTPException(status_code=429, detail="too many searches are running; try again shortly")
    async with slot:
        try:
            returncode, stdout, stderr = await run_bounded(argv, timeout=settings.youtube_search_timeout_seconds)
        except TimeoutError:
            log.warning("youtube search timed out after %ss", settings.youtube_search_timeout_seconds)
            raise HTTPException(status_code=504, detail="YouTube search timed out") from None
    if returncode != 0:
        log.warning(
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
        cause=result.cause,
    )
