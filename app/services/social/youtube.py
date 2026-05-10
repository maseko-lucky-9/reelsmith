"""YouTube Data API v3 adapter (W1.5 — only live adapter at Wave 1).

Per ADR-003 §A.1, YouTube ships the sole real OAuth path. The other
four platforms remain on the stub provider until the operator
completes their respective app-review flows.

This implementation issues a resumable-upload POST against the
Videos.insert endpoint. The actual upload uses ``httpx.AsyncClient``
(already a project dependency) so we don't pull in
``google-api-python-client`` at the W1 PR boundary; the adapter
stays small and easy to mock at the transport layer.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.services.social.base import PlatformAdapter, PublishRequest, PublishResult

log = logging.getLogger(__name__)


class YouTubeAdapter(PlatformAdapter):
    platform = "youtube"

    _UPLOAD_INIT = "https://www.googleapis.com/upload/youtube/v3/videos"

    def __init__(self, *, http: httpx.AsyncClient | None = None) -> None:
        self._http = http  # tests inject a transport-mocked client

    async def publish(self, request: PublishRequest) -> PublishResult:
        if not request.access_token:
            raise ValueError("youtube: missing access token")
        clip_path = Path(request.clip_path)
        if not clip_path.is_file():
            raise FileNotFoundError(f"clip not found: {clip_path}")

        snippet = {
            "snippet": {
                "title": request.title[:100],  # YouTube hard cap
                "description": request.description[:5000],
                "tags": list(request.hashtags)[:30],
                "categoryId": "22",  # People & Blogs
            },
            "status": {"privacyStatus": "private"},  # always start private
        }
        params = {"part": "snippet,status", "uploadType": "resumable"}
        headers = {
            "Authorization": f"Bearer {request.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(clip_path.stat().st_size),
        }

        async with self._client() as http:
            init = await http.post(
                self._UPLOAD_INIT, params=params, headers=headers, json=snippet
            )
            init.raise_for_status()
            upload_url = init.headers.get("location")
            if not upload_url:
                raise RuntimeError("youtube: resumable upload URL not returned")

            # Single-PUT upload — fine for clips up to a few hundred MB.
            with clip_path.open("rb") as fh:
                put = await http.put(
                    upload_url,
                    content=fh.read(),
                    headers={"Content-Type": "video/mp4"},
                )
                put.raise_for_status()
                body = put.json()

        external_id = body.get("id")
        if not external_id:
            raise RuntimeError(f"youtube: missing video id in response: {body!r}")
        return PublishResult(
            external_post_id=external_id,
            external_post_url=f"https://www.youtube.com/watch?v={external_id}",
        )

    def _client(self) -> httpx.AsyncClient:
        if self._http is not None:
            return _passthrough(self._http)
        return httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))


class _passthrough:
    """Wraps an externally-owned AsyncClient so ``async with`` doesn't close it."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *exc_info) -> None:
        return None
