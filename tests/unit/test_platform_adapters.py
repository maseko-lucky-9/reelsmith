"""Per-adapter contract: chapter extraction policies."""
from __future__ import annotations

import pytest

from app.services.platforms import (
    FacebookAdapter,
    InstagramAdapter,
    TikTokAdapter,
    YouTubeAdapter,
)
from app.services.platforms.base import Chapter

_CHAPTERED_INFO = {
    "chapters": [
        {"title": "Intro", "start_time": 0.0, "end_time": 30.0},
        {"title": "Main", "start_time": 30.0, "end_time": 120.0},
    ]
}


def test_youtube_extract_chapters_parses_info():
    chapters = YouTubeAdapter().extract_chapters(_CHAPTERED_INFO)
    assert chapters == [
        Chapter(index=0, title="Intro", start=0.0, end=30.0),
        Chapter(index=1, title="Main", start=30.0, end=120.0),
    ]


@pytest.mark.parametrize("info", [{}, {"chapters": None}, {"chapters": []}])
def test_youtube_extract_chapters_empty_when_absent(info):
    assert YouTubeAdapter().extract_chapters(info) == []


@pytest.mark.parametrize(
    "adapter_cls", [FacebookAdapter, TikTokAdapter, InstagramAdapter]
)
def test_short_form_adapters_return_no_chapters(adapter_cls):
    """FB/TT/IG ignore `info["chapters"]` even when populated."""
    assert adapter_cls().extract_chapters(_CHAPTERED_INFO) == []
    assert adapter_cls().extract_chapters({}) == []


# ── upload:// duration probe (PyAV; no system ffprobe needed) ────────────────


def test_upload_probe_duration_reads_real_file_and_falls_back_to_zero(tmp_path):
    from pathlib import Path

    from app.services.platforms import upload

    sample = Path(__file__).resolve().parents[1] / "fixtures" / "sample.mp4"
    assert upload._probe_duration(str(sample)) == 5.0
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    assert upload._probe_duration(str(junk)) == 0.0
    assert upload._probe_duration(str(tmp_path / "missing.mp4")) == 0.0
