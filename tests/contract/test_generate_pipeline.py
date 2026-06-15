"""Contract: POST /generate drives a real job to completion through the
existing pipeline using stub producers + stub transcription.

Marked ``e2e`` (still part of the default fast set — ``addopts`` only excludes
integration/live/playwright) because it does real MoviePy assembly + render.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.main import create_app
from app.settings import settings
from app.workers import orchestrator as orch


pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_generate_pipeline_produces_manifest_and_output(tmp_path, monkeypatch):
    brief_dir = tmp_path / "briefs"
    download_root = tmp_path / "dl"
    export_root = tmp_path / "exports"
    download_root.mkdir(parents=True, exist_ok=True)

    # Generate mode ON, both producers STUB, transcription STUB → fast + offline.
    monkeypatch.setattr(settings, "generate_enabled", True)
    monkeypatch.setattr(settings, "generate_brief_dir", str(brief_dir))
    monkeypatch.setattr(settings, "ltx_provider", "stub")
    monkeypatch.setattr(settings, "generate_tts_provider", "stub")
    monkeypatch.setattr(settings, "default_download_path", str(download_root))
    monkeypatch.setattr(settings, "job_store", "memory")
    # Pin export to a tmp dir so the manifest lands deterministically (the dev
    # .env points export_base_folder at a Syncthing share otherwise).
    monkeypatch.setattr(settings, "export_base_folder", str(export_root))
    # Keep the orchestrator's view of settings in sync.
    monkeypatch.setattr(orch.settings, "transcription_provider", "stub")
    monkeypatch.setattr(orch.settings, "ollama_enabled", False)
    monkeypatch.setattr(orch.thumbnail_service, "generate_thumbnail", lambda *a, **kw: None)

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            post_resp = await client.post(
                "/generate",
                json={
                    "title": "Generated Reel",
                    "script": "A short script about staying focused while working.",
                    "shots": [
                        {"prompt": "sunrise over a quiet city", "seconds": 1.0},
                        {"prompt": "a person writing in a notebook", "seconds": 1.0},
                    ],
                    "voice_profile": "",
                    "music_url": "",
                },
            )
            assert post_resp.status_code == 202
            body = post_resp.json()
            job_id = body["job_id"]
            assert body["brief_id"]

            seen_types: list[str] = []
            async with client.stream(
                "GET", f"/jobs/{job_id}/events", timeout=120.0
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

            assert "JobCompleted" in seen_types, f"events seen: {seen_types}"

            final_resp = await client.get(f"/jobs/{job_id}")
            state = final_resp.json()

    assert state["status"] == "completed", state.get("error")
    assert state["source"] == "generate"
    assert state["output_paths"]
    for path in state["output_paths"]:
        assert Path(path).is_file()

    # A manifest.csv with one data row must exist under the export tree.
    manifests = list(Path(export_root).rglob("manifest.csv"))
    assert manifests, "no manifest.csv produced"
    rows = [
        ln for ln in manifests[0].read_text().splitlines() if ln.strip()
    ]
    # header + exactly one clip row (single full-video pseudo-chapter)
    assert len(rows) == 2

    # At least one output mp4 exists.
    assert any(Path(p).suffix == ".mp4" for p in state["output_paths"])
