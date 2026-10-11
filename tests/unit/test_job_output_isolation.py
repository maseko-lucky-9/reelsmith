"""Each job writes into its own output folder (T030).

Before the fix every ``upload://`` job used ``<base>/upload_video/clips`` and
every ``generate://`` job ``<base>/generate_video/clips``, so a later job
overwrote an earlier job's clip file while the earlier job's row still
pointed at it. Same-title YouTube videos collided the same way.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.bus.event_bus import AsyncEventBus
from app.bus.job_store import JobStore
from app.domain.events import Event, EventType
from app.domain.ids import new_job_id
from app.domain.models import JobState
from app.services.folder_service import create_video_subfolder
from app.workers import orchestrator as orch

UPLOAD_URL = "upload:///tmp/yt/uploads/x.mp4"
CLIP_NAME = "00_Full Video.mp4"


def _write_clip(clips_folder: str, data: bytes) -> Path:
    path = Path(clips_folder) / CLIP_NAME
    path.write_bytes(data)
    return path


def test_upload_jobs_get_distinct_clip_paths(tmp_path):
    job_a, job_b = new_job_id(), new_job_id()

    _, clips_a = create_video_subfolder(
        str(tmp_path), UPLOAD_URL, "upload", job_id=job_a
    )
    _, clips_b = create_video_subfolder(
        str(tmp_path), UPLOAD_URL, "upload", job_id=job_b
    )
    clip_a = _write_clip(clips_a, b"job a")
    clip_b = _write_clip(clips_b, b"job b")

    assert clip_a != clip_b
    # The later job did not overwrite the earlier job's file.
    assert clip_a.read_bytes() == b"job a"
    assert clip_b.read_bytes() == b"job b"


def test_folder_name_is_slug_plus_first_eight_chars_of_job_id(tmp_path):
    job_id = new_job_id()

    video_folder, clips_folder = create_video_subfolder(
        str(tmp_path), "generate://x", "generate", job_id=job_id
    )

    assert os.path.basename(video_folder) == f"generate_video-{job_id[:8]}"
    assert clips_folder == os.path.join(video_folder, "clips")
    assert os.path.isdir(clips_folder)


@patch("app.services.folder_service.YoutubeDL")
def test_same_title_youtube_jobs_get_distinct_folders(mock_ydl_cls, tmp_path):
    instance = MagicMock()
    instance.__enter__.return_value.extract_info.return_value = {"title": "Same Title"}
    mock_ydl_cls.return_value = instance
    url = "https://www.youtube.com/watch?v=abc"

    folder_a, _ = create_video_subfolder(
        str(tmp_path), url, "youtube", job_id=new_job_id()
    )
    folder_b, _ = create_video_subfolder(
        str(tmp_path), url, "youtube", job_id=new_job_id()
    )

    assert folder_a != folder_b
    assert os.path.basename(folder_a).startswith("Same_Title-")


async def test_orchestrator_passes_the_job_id_to_the_folder_service(
    tmp_path, monkeypatch
):
    """The orchestrator must hand its job id to create_video_subfolder, or
    every job falls back to the shared, unsuffixed folder."""
    seen: dict[str, object] = {}

    def _spy(download_path, url, platform_id="video", job_id=None):
        seen["job_id"] = job_id
        clips = tmp_path / "vid" / "clips"
        clips.mkdir(parents=True, exist_ok=True)
        return str(clips.parent), str(clips)

    class _FailingAdapter:
        platform_id = "upload"

        def download(self, url, destination_folder):
            raise RuntimeError("stop after the folder step")

    monkeypatch.setattr(orch.folder_service, "create_video_subfolder", _spy)
    monkeypatch.setattr(orch, "resolve_adapter", lambda url: _FailingAdapter())

    bus, store = AsyncEventBus(), JobStore()
    job_id = new_job_id()
    await store.create(
        JobState(job_id=job_id, url=UPLOAD_URL, download_path=str(tmp_path))
    )
    trigger = Event(
        type=EventType.VIDEO_REQUESTED,
        job_id=job_id,
        payload={"url": UPLOAD_URL, "download_path": str(tmp_path)},
    )

    await orch._run_job(trigger, bus, store)

    assert seen["job_id"] == job_id
