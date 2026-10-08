"""Every per-chapter stage log line carries a ``(N.NNs)`` step timing.

``scripts/bench.py`` derives its per-stage table from these lines, so a stage
that logs without a timing silently vanishes from the benchmark.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from app.bus.job_store import JobStore
from app.bus.event_bus import AsyncEventBus
from app.domain.models import JobState, PipelineOptions
from app.services.transcription_service import WordTiming
from app.workers import orchestrator as orch

_TIMED = re.compile(r"\(\d+\.\d{2}s\)")


def _touch(path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"\x00")
    return path


@pytest.fixture
def stubbed_services(monkeypatch):
    monkeypatch.setattr(
        orch.clip_service,
        "extract_audio",
        lambda src, start, duration, wav: _touch(wav),
    )
    monkeypatch.setattr(
        orch.audio_enhance_service, "enhance", lambda src, dst, **_: _touch(dst)
    )
    monkeypatch.setattr(
        orch.transcription_service,
        "transcribe_to_words",
        lambda path: [WordTiming("hello", 0.0, 0.5), WordTiming("world", 0.5, 1.0)],
    )
    monkeypatch.setattr(
        orch.render_service, "render_clip", lambda src, out, *a, **k: _touch(out)
    )
    monkeypatch.setattr(
        orch.thumbnail_service, "generate_thumbnail", lambda clip, out: _touch(out)
    )
    monkeypatch.setattr(orch.settings, "ollama_enabled", False)


async def test_thumbnail_and_stage_lines_log_step_timing(
    tmp_path, caplog, stubbed_services
):
    store = JobStore()
    await store.create(
        JobState(
            job_id="j",
            url="upload://x.mp4",
            source="upload",
            download_path=str(tmp_path),
        )
    )
    cleanup = tmp_path / "tmp"
    cleanup.mkdir()

    with caplog.at_level(logging.INFO, logger=orch.log.name):
        out = await orch._process_chapter(
            chapter={"index": 0, "title": "c", "start": 0.0, "end": 2.0},
            job_id="j",
            video_path=str(tmp_path / "src.mp4"),
            clips_folder=str(tmp_path / "clips"),
            cleanup_root=cleanup,
            caption_format="srt",
            target_aspect_ratio=9 / 16,
            bus=AsyncEventBus(),
            store=store,
            pipeline_options=PipelineOptions(),
        )

    assert out is not None
    messages = [r.getMessage() for r in caplog.records if r.name == orch.log.name]
    thumb = [m for m in messages if "thumbnail generated" in m]
    assert len(thumb) == 1
    assert _TIMED.search(thumb[0]), thumb[0]

    for marker in (
        "audio extracted",
        "audio enhanced",
        "transcription done",
        "captions written",
        "render done",
    ):
        lines = [m for m in messages if marker in m]
        assert lines and _TIMED.search(lines[0]), (marker, lines)
