"""End-to-end test against a stubbed download/render pipeline.

Patches the heavy services so the test is deterministic on Apple Silicon
without ffmpeg / network. Verifies the SSE stream terminates in JobCompleted
and that output paths exist on disk.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.main import create_app
from app.workers import orchestrator as orch


pytestmark = pytest.mark.e2e


def _fake_subfolder(download_path: str, url: str):
    base = Path(download_path) / "vid"
    clips = base / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    return str(base), str(clips)


def _fake_download(url: str, destination_folder: str):
    video_path = Path(destination_folder) / "video.mp4"
    video_path.write_bytes(b"\x00")
    info = {
        "title": "Test",
        "duration": 6.0,
        "chapters": [{"title": "Only", "start_time": 0.0, "end_time": 6.0}],
    }
    return str(video_path), info


def _fake_extract_chapter(video_path, start, end, out_clip, out_audio):
    Path(out_clip).parent.mkdir(parents=True, exist_ok=True)
    Path(out_clip).write_bytes(b"\x00")
    Path(out_audio).write_bytes(b"\x00")
    return out_clip, out_audio


def _fake_render(video_path, output_path, *args, **kwargs):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(b"\x00")
    return output_path


def _fake_render_to_path(text, videosize, path, font_size=50):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"\x00")
    return path


@pytest.mark.asyncio
async def test_happy_path_emits_completed(tmp_path, monkeypatch):
    monkeypatch.setattr(orch.folder_service, "create_video_subfolder", _fake_subfolder)
    monkeypatch.setattr(orch.download_service, "download_video", _fake_download)
    monkeypatch.setattr(orch.clip_service, "extract_chapter_to_disk", _fake_extract_chapter)
    from app.services.transcription_service import WordTiming
    monkeypatch.setattr(
        orch.transcription_service, "transcribe_to_words",
        lambda audio_path, language="en": [
            WordTiming("hello", 0.0, 0.5),
            WordTiming("world", 0.5, 1.0),
        ],
    )
    monkeypatch.setattr(orch.render_service, "render_clip", _fake_render)
    monkeypatch.setattr(orch.subtitle_image_service, "render_to_path", _fake_render_to_path)
    monkeypatch.setattr(orch.settings, "ollama_enabled", False)
    monkeypatch.setattr(orch.thumbnail_service, "generate_thumbnail", lambda *a, **kw: None)
    # Use in-memory store to avoid stale SQL state returning old job_ids for the
    # same test URL, which causes the SSE handler to wait forever for events that
    # will never arrive.
    monkeypatch.setattr(orch.settings, "job_store", "memory")

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            post_resp = await client.post(
                "/jobs",
                json={"url": "https://example.com/v", "download_path": str(tmp_path)},
            )
            assert post_resp.status_code == 202
            job_id = post_resp.json()["job_id"]

            seen_types: list[str] = []
            async with client.stream(
                "GET", f"/jobs/{job_id}/events", timeout=15.0
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

            assert "JobCompleted" in seen_types
            final_resp = await client.get(f"/jobs/{job_id}")
            state = final_resp.json()

    assert state["status"] == "completed"
    assert state["output_paths"]
    for path in state["output_paths"]:
        assert Path(path).is_file()
