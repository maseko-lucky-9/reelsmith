from pathlib import Path

import pytest
from unittest.mock import MagicMock

from app.services.clip_service import (
    AUDIO_TAIL_EPSILON_SECONDS,
    create_clip,
    extract_chapter_to_disk,
    probe_safe_end,
)


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample.mp4"


def test_valid_clip_creation():
    video = MagicMock()
    video.subclip.return_value = "clip"
    assert create_clip(video, 10, 20) == "clip"


def test_invalid_start_time():
    video = MagicMock()
    with pytest.raises(ValueError):
        create_clip(video, -10, 20)


def test_invalid_end_time():
    video = MagicMock()
    with pytest.raises(ValueError):
        create_clip(video, 10, -20)


def test_start_time_greater_than_end_time():
    video = MagicMock()
    with pytest.raises(ValueError):
        create_clip(video, 20, 10)


def test_start_time_equal_to_end_time():
    video = MagicMock()
    with pytest.raises(ValueError):
        create_clip(video, 10, 10)


def test_video_object_without_subclip_method():
    video = object()
    with pytest.raises(AttributeError):
        create_clip(video, 10, 20)


# ── Audio-EOF boundary clamp (regression for OSError t=596.55 / dur=596) ──────


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture sample.mp4 missing")
def test_probe_safe_end_subtracts_epsilon():
    safe = probe_safe_end(str(FIXTURE))
    # sample.mp4 is 5.0s with audio; safe_end == 5.0 - 1.0 = 4.0
    assert safe == pytest.approx(5.0 - AUDIO_TAIL_EPSILON_SECONDS, abs=0.05)


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture sample.mp4 missing")
def test_extract_chapter_to_disk_clamps_when_end_at_audio_eof(tmp_path):
    """Regression: requesting end == audio EOF used to raise OSError from
    AudioFileClip.write_audiofile reading past `duration`. The defensive clamp
    inside extract_chapter_to_disk must clip `end` below EOF so the call
    succeeds."""
    out_clip = tmp_path / "out.mp4"
    out_audio = tmp_path / "out.wav"
    extract_chapter_to_disk(str(FIXTURE), 0.0, 5.0, str(out_clip), str(out_audio))
    assert out_clip.exists() and out_clip.stat().st_size > 0
    assert out_audio.exists() and out_audio.stat().st_size > 0


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture sample.mp4 missing")
def test_extract_chapter_to_disk_raises_when_window_collapses(tmp_path):
    out_clip = tmp_path / "out.mp4"
    out_audio = tmp_path / "out.wav"
    # start past the audio-safe ceiling — clamp collapses the window.
    with pytest.raises(ValueError):
        extract_chapter_to_disk(
            str(FIXTURE), 4.5, 5.0, str(out_clip), str(out_audio)
        )
