"""Pexels B-roll provider (FR-010, T012): one video per search query.

``fetch_asset(query)`` searches Pexels videos
(``GET https://api.pexels.com/videos/search``, the base URL of Pexels'
official JS client; the API documentation at
https://www.pexels.com/api/documentation/#videos-search lists the response
fields used here), picks a file, downloads it into
``settings.broll_cache_dir`` and returns a ``BrollAsset`` with the
videographer's name and the video's Pexels page for the credit.

* The key is ``settings.pexels_api_key``, read on every call and sent only
  as the search request's ``Authorization`` header: never to the download
  host, never logged. No key: None at once, without a request.
* File choice: the first video (Pexels' relevance order, ``orientation=
  portrait``) with an ``video/mp4`` file at most ``MAX_FILE_WIDTH`` wide on an
  allowed host; among its files a portrait one (height > width) wins, then
  the widest.
* Downloads only from ``https://pexels.com`` or a ``*.pexels.com`` host on the
  default port (an SSRF guard: the link comes from the API response).
  Redirects are not followed. At most ``max_bytes`` (50 MB) are read: a
  larger ``Content-Length`` is refused before reading, a longer stream is
  cut while streaming. Explicit httpx timeouts plus an overall download
  deadline.
* Cache (``<broll_cache_dir>/pexels``): ``videos/<id>.mp4`` and
  ``videos/<id>.json`` (credit) per Pexels video id, ``queries/<hash>.json``
  maps a query to its video id. A cached query with its video on disk needs
  no network; a new query that finds a cached video skips the download.
  Every file is written to a temp name and renamed into place.
* Any HTTP error, timeout, bad body or disk error: logged, None.
  Cancellation propagates.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.services.broll_service import BrollAsset
from app.settings import settings

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.pexels.com/videos/search"
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_FILE_WIDTH = 1080
SEARCH_RESULTS = 5
DOWNLOAD_DEADLINE_SECONDS = 120.0
_SEARCH_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_PROVIDER = "pexels"


def is_allowed_download(link: Any) -> bool:
    """True for an https URL on ``pexels.com`` or a subdomain, default port."""
    try:
        url = httpx.URL(str(link))
    except (httpx.InvalidURL, TypeError, ValueError):
        return False
    host = (url.host or "").lower()
    return (
        url.scheme == "https"
        and url.port in (None, 443)
        and (host == "pexels.com" or host.endswith(".pexels.com"))
    )


def _video_id(video: dict[str, Any]) -> str | None:
    raw = video.get("id")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return None
    return str(raw)


def _pick_file(video: dict[str, Any]) -> dict[str, Any] | None:
    files = []
    for f in video.get("video_files") or []:
        if not isinstance(f, dict) or f.get("file_type") != "video/mp4":
            continue
        width, height = f.get("width"), f.get("height")
        if not (isinstance(width, int) and isinstance(height, int)):
            continue
        if not 0 < width <= MAX_FILE_WIDTH or height <= 0:
            continue
        if not is_allowed_download(f.get("link")):
            log.warning(
                "Pexels link %r is not an allowed Pexels download; skipped",
                f.get("link"),
            )
            continue
        files.append(f)
    if not files:
        return None
    return max(files, key=lambda f: (f["height"] > f["width"], f["width"], f["height"]))


def _credit_url(video: dict[str, Any], asset_id: str) -> str:
    url = video.get("url")
    if isinstance(url, str) and is_allowed_download(url):
        return url
    return f"https://www.pexels.com/video/{asset_id}/"


def _author(video: dict[str, Any]) -> str:
    user = video.get("user")
    name = user.get("name") if isinstance(user, dict) else None
    return name if isinstance(name, str) else ""


def _write_atomically(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _query_file(root: Path, query: str) -> Path:
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:24]
    return root / "queries" / f"{digest}.json"


def _cached_asset(root: Path, asset_id: str) -> BrollAsset | None:
    video = root / "videos" / f"{asset_id}.mp4"
    meta = root / "videos" / f"{asset_id}.json"
    if not (video.is_file() and meta.is_file()):
        return None
    try:
        credit = json.loads(meta.read_text(encoding="utf-8"))
        return BrollAsset(
            path=str(video),
            provider=_PROVIDER,
            asset_id=asset_id,
            author=str(credit["author"]),
            source_url=str(credit["source_url"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _cached_query(root: Path, query: str) -> BrollAsset | None:
    try:
        entry = json.loads(_query_file(root, query).read_text(encoding="utf-8"))
        asset_id = str(entry["asset_id"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return _cached_asset(root, asset_id) if asset_id.isdigit() else None


async def _search(
    client: httpx.AsyncClient, query: str, api_key: str
) -> list[Any] | None:
    response = await client.get(
        SEARCH_URL,
        params={"query": query, "orientation": "portrait", "per_page": SEARCH_RESULTS},
        headers={"Authorization": api_key},
        timeout=_SEARCH_TIMEOUT,
        follow_redirects=False,
    )
    if response.status_code != 200:
        hint = {
            401: " (check YTVIDEO_PEXELS_API_KEY)",
            403: " (check YTVIDEO_PEXELS_API_KEY)",
            429: " (rate limit reached)",
        }.get(response.status_code, "")
        log.warning(
            "Pexels search for %r failed: HTTP %d%s", query, response.status_code, hint
        )
        return None
    body = response.json()
    videos = body.get("videos") if isinstance(body, dict) else None
    return videos if isinstance(videos, list) else []


async def _download(
    client: httpx.AsyncClient, link: str, dest: Path, max_bytes: int
) -> bool:
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        async with asyncio.timeout(DOWNLOAD_DEADLINE_SECONDS):
            async with client.stream(
                "GET", link, timeout=_DOWNLOAD_TIMEOUT, follow_redirects=False
            ) as response:
                if response.status_code != 200:
                    log.warning(
                        "Pexels download %s failed: HTTP %d", link, response.status_code
                    )
                    return False
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > max_bytes:
                    log.warning(
                        "Pexels download %s too large: %s bytes > %d",
                        link,
                        declared,
                        max_bytes,
                    )
                    return False
                size = 0
                with tmp.open("wb") as fh:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            log.warning(
                                "Pexels download %s too large: over %d bytes; aborted",
                                link,
                                max_bytes,
                            )
                            return False
                        fh.write(chunk)
        os.replace(tmp, dest)
        return True
    finally:
        tmp.unlink(missing_ok=True)


async def _fetch(
    client: httpx.AsyncClient, query: str, api_key: str, root: Path, max_bytes: int
) -> BrollAsset | None:
    videos = await _search(client, query, api_key)
    if videos is None:
        return None
    for video in videos:
        if not isinstance(video, dict):
            continue
        asset_id = _video_id(video)
        if asset_id is None:
            log.warning(
                "Pexels result without a numeric id skipped: %r", video.get("id")
            )
            continue
        chosen = _pick_file(video)
        if chosen is None:
            continue
        break
    else:
        log.info("Pexels has no usable video for %r", query)
        return None

    videos_dir = root / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    dest = videos_dir / f"{asset_id}.mp4"
    if not dest.is_file():
        if not await _download(client, chosen["link"], dest, max_bytes):
            return None
    credit = {"author": _author(video), "source_url": _credit_url(video, asset_id)}
    _write_atomically(
        videos_dir / f"{asset_id}.json", json.dumps(credit).encode("utf-8")
    )
    query_file = _query_file(root, query)
    query_file.parent.mkdir(parents=True, exist_ok=True)
    _write_atomically(
        query_file, json.dumps({"query": query, "asset_id": asset_id}).encode("utf-8")
    )
    log.info("Pexels B-roll for %r: video %s by %s", query, asset_id, credit["author"])
    return BrollAsset(
        path=str(dest),
        provider=_PROVIDER,
        asset_id=asset_id,
        author=credit["author"],
        source_url=credit["source_url"],
    )


async def fetch_asset(
    query: str,
    *,
    client: httpx.AsyncClient | None = None,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> BrollAsset | None:
    """A cached Pexels video for ``query``, or None (see the module docstring)."""
    api_key = settings.pexels_api_key
    if not api_key:
        log.warning("Pexels B-roll skipped: YTVIDEO_PEXELS_API_KEY is not set")
        return None
    query = " ".join(query.split()).lower()
    if not query:
        return None
    root = Path(settings.broll_cache_dir) / _PROVIDER
    cached = _cached_query(root, query)
    if cached is not None:
        log.info(
            "Pexels B-roll for %r from the cache: video %s", query, cached.asset_id
        )
        return cached
    try:
        if client is not None:
            return await _fetch(client, query, api_key, root, max_bytes)
        async with httpx.AsyncClient(follow_redirects=False) as own:
            return await _fetch(own, query, api_key, root, max_bytes)
    except (httpx.HTTPError, TimeoutError, ValueError, OSError) as e:
        log.warning("Pexels B-roll for %r failed: %s: %s", query, type(e).__name__, e)
        return None
