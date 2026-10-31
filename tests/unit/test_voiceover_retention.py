"""A clip's voice-over wavs (spec 016) live in the clip's folder, so deleting
the job (``retention.delete_job_files``) removes them with the rest of its
folder, including one no timeline references any more."""

from __future__ import annotations

from pathlib import Path

from app.bus.job_store import InMemoryJobStore
from app.domain.models import JobState
from app.services import retention, voiceover_service
from app.services.retention import delete_job_files

JOB = "job-voice-0001"


async def test_job_delete_removes_voiceover_wavs(tmp_path: Path, monkeypatch):
    root = (tmp_path / "downloads").resolve()
    (root / "uploads").mkdir(parents=True)
    monkeypatch.setattr(retention.settings, "default_download_path", str(root))
    monkeypatch.setattr(retention.settings, "export_base_folder", str(tmp_path / "share"))
    folder = root / f"Talk-{JOB[:8]}"
    clips = folder / "clips"
    clips.mkdir(parents=True)
    reel = clips / "00_Talk.mp4"
    reel.write_bytes(b"reel")
    used = voiceover_service.reserve_output_path(clips)
    orphan = voiceover_service.reserve_output_path(clips)
    for wav in (used, orphan):
        voiceover_service.synthesize_voiceover("hi", "en_US-lessac-medium", wav, provider="stub", model_dir=tmp_path)

    store = InMemoryJobStore()
    job = await store.create(
        JobState(
            job_id=JOB, url="https://www.youtube.com/watch?v=x", download_path="/unused", destination_folder=str(folder)
        )
    )
    await store.upsert_clip(JOB, "c1", lambda c: c.update(output_path=str(reel)))

    deleted = await delete_job_files(job, store)

    assert {str(used), str(orphan)} <= set(deleted)
    assert not used.exists() and not orphan.exists() and not folder.exists()
