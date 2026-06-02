"""TikTok publish adapter that routes through the existing n8n tiktok-sidecar.

This is the SAFE DEFAULT adapter — it reuses the sidecar's session cookie,
rate cap (≤3/day), and single source of truth, so it can safely post to
@reelsmith even while the n8n cron queue is draining.

Requires ``settings.tiktok_sidecar_url`` to be set (e.g.
``http://tiktok-sidecar.n8n-live.svc.cluster.local:8000/api/upload``).
See ``docs/social-publish-handoff.md:119-124`` for the sidecar contract.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.services.social.base import PublishRequest, PublishResult
from app.settings import settings

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(300.0, connect=10.0)  # uploads can be large


class N8nTikTokAdapter:
    """Immediate-post via the tiktok-sidecar /api/upload endpoint."""

    platform = "tiktok"

    def __init__(self, *, sidecar_url: str | None = None, http: httpx.AsyncClient | None = None) -> None:
        # sidecar_url / http are injectable for tests; None → read from settings / create fresh client
        self._sidecar_url = sidecar_url
        self._http = http

    async def publish(self, request: PublishRequest) -> PublishResult:
        sidecar_url = self._sidecar_url if self._sidecar_url is not None else settings.tiktok_sidecar_url
        if not sidecar_url:
            raise RuntimeError(
                "tiktok(n8n): tiktok_sidecar_url is not configured — "
                "set YTVIDEO_TIKTOK_SIDECAR_URL in .env"
            )

        clip_path = Path(request.clip_path)
        if not clip_path.is_file():
            raise FileNotFoundError(f"tiktok(n8n): clip not found: {clip_path}")

        caption = request.description or request.title
        hashtags = " ".join(f"#{h.lstrip('#')}" for h in request.hashtags)

        async with (self._http or httpx.AsyncClient(timeout=_TIMEOUT)) as http:
            with clip_path.open("rb") as fh:
                resp = await http.post(
                    sidecar_url,
                    files={"video": (clip_path.name, fh, "video/mp4")},
                    data={
                        "caption": caption,
                        "hashtags": hashtags,
                        "account": request.account_handle.lstrip("@"),
                    },
                )

        if resp.status_code >= 400:
            raise RuntimeError(
                f"tiktok(n8n): sidecar returned {resp.status_code}: {resp.text[:200]}"
            )

        body = resp.json()
        post_id = body.get("post_id") or body.get("id") or f"n8n_{clip_path.stem}"
        post_url = body.get("post_url") or body.get("url")
        return PublishResult(external_post_id=str(post_id), external_post_url=post_url)
