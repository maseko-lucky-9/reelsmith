"""Live end-to-end test against the canonical YouTube URL.

Opt-in only: ``pytest -m live``.

Costs real Google Speech-to-Text quota (~50/day per IP) and downloads
several hundred MB of video. Expected runtime on M5 Pro with
``max_parallel_chapters=2``: under 2x source-video duration.

Set ``YTVIDEO_TRANSCRIPTION_PROVIDER=stub`` to skip Google STT.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.main import create_app


pytestmark = [pytest.mark.live, pytest.mark.asyncio]


CANONICAL_URL = "https://www.youtube.com/watch?v=8I3_NM-V_w0"


@pytest.mark.timeout(1800)
async def test_canonical_youtube_url_renders_clips(tmp_path):
    if os.environ.get("YTVIDEO_TRANSCRIPTION_PROVIDER") is None:
        os.environ["YTVIDEO_TRANSCRIPTION_PROVIDER"] = "stub"

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            post_resp = await client.post(
                "/jobs",
                json={"url": CANONICAL_URL, "download_path": str(tmp_path)},
            )
            assert post_resp.status_code == 202
            job_id = post_resp.json()["job_id"]

            seen_types: list[str] = []
            async with client.stream(
                "GET", f"/jobs/{job_id}/events", timeout=1500.0
            ) as stream:
                current_event = None
                async for raw_line in stream.aiter_lines():
                    if raw_line.startswith("event:"):
                        current_event = raw_line.split(":", 1)[1].strip()
                    elif raw_line.startswith("data:") and current_event:
                        seen_types.append(current_event)
                        if current_event in {"JobCompleted", "JobFailed"}:
                            break
                        current_event = None

            final_resp = await client.get(f"/jobs/{job_id}")
            state = final_resp.json()

    assert state["status"] == "completed", state.get("error")
    assert state["output_paths"]
    for path in state["output_paths"]:
        assert Path(path).is_file()
