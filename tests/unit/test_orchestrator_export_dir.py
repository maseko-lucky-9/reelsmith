"""Verify export_dir namespacing logic in the orchestrator export step."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import JobStore
from app.domain.events import Event, EventType
from app.domain.models import JobState
from app.workers import orchestrator as orch


def _wire_stubs(monkeypatch, tmp_path: Path, export_base: str = "") -> str:
    """Patch all services so _run_job reaches the export step without real I/O."""
    monkeypatch.setattr(orch.settings, "export_base_folder", export_base)
    monkeypatch.setattr(orch.settings, "max_parallel_chapters", 1)

    vid_dir = tmp_path / "vid"
    clips_dir = vid_dir / "clips"
    clips_dir.mkdir(parents=True)

    monkeypatch.setattr(
        orch.folder_service, "create_video_subfolder",
        lambda *a, **kw: (str(vid_dir), str(clips_dir)),
    )
    monkeypatch.setattr(
        orch.download_service, "download_video",
        lambda *a, **kw: (
            str(vid_dir / "video.mp4"),
            {"title": "T", "duration": 6.0, "chapters": [
                {"title": "C", "start_time": 0.0, "end_time": 6.0}
            ]},
        ),
    )
    monkeypatch.setattr(
        orch.download_service, "extract_chapters",
        lambda info: [{"index": 0, "title": "C", "start": 0.0, "end": 6.0}],
    )

    fake_clip = str(vid_dir / "clips" / "chapter_0.mp4")

    async def _fake_process_chapter(**kwargs):
        return fake_clip

    monkeypatch.setattr(orch, "_process_chapter", _fake_process_chapter)

    return str(clips_dir)


async def _run(job_id: str, tmp_path: Path) -> None:
    bus = AsyncEventBus()
    store = JobStore()
    state = JobState(job_id=job_id, url="https://example.com/v", download_path=str(tmp_path))
    await store.create(state)

    trigger = Event(
        type=EventType.VIDEO_REQUESTED,
        job_id=job_id,
        payload={
            "url": "https://example.com/v",
            "download_path": str(tmp_path),
            "caption_format": "srt",
            "target_aspect_ratio": 9 / 16,
        },
    )
    await orch._run_job(trigger=trigger, bus=bus, store=store)


@pytest.mark.asyncio
async def test_export_dir_namespaced_when_base_folder_set(tmp_path, monkeypatch):
    export_base = str(tmp_path / "exports")
    _wire_stubs(monkeypatch, tmp_path, export_base=export_base)

    captured: list[str] = []

    monkeypatch.setattr(
        orch.export_service, "export_clips",
        lambda paths, export_dir: (captured.append(export_dir), [p for p in paths if p])[1],
    )
    monkeypatch.setattr(
        orch.manifest_service, "write_manifest",
        lambda clips_data, export_dir: str(Path(export_dir) / "manifest.csv"),
    )

    job_id = "job-namespaced"
    await _run(job_id, tmp_path)

    assert len(captured) == 1
    assert captured[0] == str(Path(export_base) / job_id)


@pytest.mark.asyncio
async def test_export_dir_uses_clips_parent_when_base_folder_unset(tmp_path, monkeypatch):
    clips_dir_str = _wire_stubs(monkeypatch, tmp_path, export_base="")

    captured: list[str] = []

    monkeypatch.setattr(
        orch.export_service, "export_clips",
        lambda paths, export_dir: (captured.append(export_dir), [p for p in paths if p])[1],
    )
    monkeypatch.setattr(
        orch.manifest_service, "write_manifest",
        lambda clips_data, export_dir: str(Path(export_dir) / "manifest.csv"),
    )

    job_id = "job-default"
    await _run(job_id, tmp_path)

    assert len(captured) == 1
    assert captured[0] == str(Path(clips_dir_str).parent / "exports")
