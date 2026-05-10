"""Default stub adapter — writes a JSON descriptor instead of posting.

Used by all platforms when ``YTVIDEO_SOCIAL_PROVIDER=stub`` (default
W1.5). Stays useful as a deterministic CI/dev path.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.services.social.base import PlatformAdapter, PublishRequest, PublishResult


class StubAdapter(PlatformAdapter):
    def __init__(self, platform: str) -> None:
        self.platform = platform

    async def publish(self, request: PublishRequest) -> PublishResult:
        out_dir = Path(request.stub_dir or "data/social-stubs")
        out_dir.mkdir(parents=True, exist_ok=True)

        post_id = f"stub_{self.platform}_{uuid.uuid4().hex[:12]}"
        descriptor = {
            "platform": self.platform,
            "account_handle": request.account_handle,
            "clip_path": request.clip_path,
            "title": request.title,
            "description": request.description,
            "hashtags": list(request.hashtags),
            "external_post_id": post_id,
            "posted_at": datetime.now(timezone.utc).isoformat(),
        }
        (out_dir / f"{post_id}.json").write_text(json.dumps(descriptor, indent=2))
        return PublishResult(
            external_post_id=post_id,
            external_post_url=f"stub://{self.platform}/{post_id}",
        )
