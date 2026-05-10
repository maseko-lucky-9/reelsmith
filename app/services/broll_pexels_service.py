"""Pexels-backed B-Roll provider (W1.9).

Search-by-keyword, with a filesystem LRU cache under
``data/broll-cache/``. Default opt-in; ``broll_provider=pexels``
flips it on. Pexels free tier is 200 requests/hour, so we cache
search responses for 7 days and downloaded videos indefinitely.

Returns a structured asset list suitable for ``clips.broll_assets``:

    [
        {"source": "pexels", "asset_id": "1234", "url": "...",
         "local_path": "data/broll-cache/1234.mp4",
         "photographer": "Jane Doe", "photographer_url": "...",
         "page_url": "https://www.pexels.com/video/1234/"},
        ...
    ]

The ``manifest_service`` consumes this list to satisfy attribution.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

_API_BASE = "https://api.pexels.com/videos"
_DEFAULT_CACHE_DIR = Path("data/broll-cache")
_SEARCH_TTL_SECONDS = 7 * 24 * 3600


class PexelsError(RuntimeError):
    pass


def _cache_root(override: str | Path | None = None) -> Path:
    root = Path(override) if override else _DEFAULT_CACHE_DIR
    (root / "search").mkdir(parents=True, exist_ok=True)
    (root / "videos").mkdir(parents=True, exist_ok=True)
    return root


def _search_cache_key(query: str, per_page: int) -> str:
    h = hashlib.sha1(f"{query}|{per_page}".encode("utf-8")).hexdigest()[:16]
    return h


def _load_search_cache(root: Path, key: str) -> dict[str, Any] | None:
    path = root / "search" / f"{key}.json"
    if not path.is_file():
        return None
    try:
        body = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    if time.time() - body.get("_cached_at", 0) > _SEARCH_TTL_SECONDS:
        return None
    return body


def _save_search_cache(root: Path, key: str, body: dict[str, Any]) -> None:
    body = dict(body)
    body["_cached_at"] = time.time()
    (root / "search" / f"{key}.json").write_text(json.dumps(body))


def search(
    query: str,
    api_key: str,
    *,
    per_page: int = 5,
    cache_dir: str | Path | None = None,
    http: httpx.Client | None = None,
) -> dict[str, Any]:
    if not query.strip():
        return {"videos": []}
    root = _cache_root(cache_dir)
    key = _search_cache_key(query.strip().lower(), per_page)
    cached = _load_search_cache(root, key)
    if cached is not None:
        return cached

    headers = {"Authorization": api_key}
    params = {"query": query, "per_page": per_page}
    client = http or httpx.Client(timeout=15.0)
    try:
        resp = client.get(f"{_API_BASE}/search", params=params, headers=headers)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise PexelsError(f"pexels search failed: {exc}") from exc
    finally:
        if http is None:
            client.close()
    _save_search_cache(root, key, body)
    return body


def _pick_smallest_video_file(video: dict[str, Any]) -> dict[str, Any] | None:
    """Return the smallest .mp4 to keep cache footprint manageable."""
    files = [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4"]
    if not files:
        return None
    files.sort(key=lambda f: (f.get("height", 9999), f.get("width", 9999)))
    return files[0]


def fetch_asset(
    query: str,
    api_key: str,
    *,
    cache_dir: str | Path | None = None,
    http: httpx.Client | None = None,
) -> dict[str, Any] | None:
    """Search Pexels for ``query`` and return the first downloadable asset.

    Returns a dict suitable for ``clips.broll_assets`` or None when no
    results match. Downloads the video bytes into the local cache.
    """
    body = search(query, api_key, cache_dir=cache_dir, http=http)
    videos = body.get("videos") or []
    if not videos:
        return None
    video = videos[0]
    file = _pick_smallest_video_file(video)
    if file is None:
        return None

    root = _cache_root(cache_dir)
    asset_id = str(video.get("id"))
    local = root / "videos" / f"{asset_id}.mp4"

    if not local.is_file():
        client = http or httpx.Client(timeout=60.0)
        try:
            r = client.get(file["link"])
            r.raise_for_status()
            local.write_bytes(r.content)
        except Exception as exc:  # noqa: BLE001
            raise PexelsError(f"pexels download failed: {exc}") from exc
        finally:
            if http is None:
                client.close()

    return {
        "source": "pexels",
        "asset_id": asset_id,
        "url": file["link"],
        "local_path": str(local),
        "photographer": video.get("user", {}).get("name", ""),
        "photographer_url": video.get("user", {}).get("url", ""),
        "page_url": video.get("url", f"https://www.pexels.com/video/{asset_id}/"),
        "duration": video.get("duration", 0),
    }
