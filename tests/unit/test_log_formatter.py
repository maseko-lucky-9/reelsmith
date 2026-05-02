"""Unit tests for ui/log_formatter.py — no Streamlit import required."""
from __future__ import annotations

import sys
from pathlib import Path

# Make ui/ importable from the test suite
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ui"))

from log_formatter import format_event  # noqa: E402

TS = "12:34:56"


def _e(etype: str, **payload) -> dict:
    return {"type": etype, "payload": payload}


def test_timestamp_prefix():
    line = format_event(_e("JobCompleted", output_paths=[]), ts=TS)
    assert line.startswith(f"[{TS}]")


def test_timestamp_defaults_to_now():
    line = format_event(_e("JobCompleted", output_paths=[]))
    assert line.startswith("[") and "]" in line


def test_video_requested():
    line = format_event(_e("VideoRequested", url="https://youtu.be/abc"), ts=TS)
    assert "Job accepted" in line
    assert "https://youtu.be/abc" in line


def test_folder_created():
    line = format_event(_e("FolderCreated", destination_folder="/tmp/vid"), ts=TS)
    assert "Folder created" in line
    assert "/tmp/vid" in line


def test_video_downloaded():
    line = format_event(_e("VideoDownloaded", title="My Video", duration=342), ts=TS)
    assert "Video downloaded" in line
    assert "My Video" in line
    assert "342s" in line


def test_chapters_detected_shows_count():
    chapters = [{"index": i} for i in range(3)]
    line = format_event(_e("ChaptersDetected", chapters=chapters), ts=TS)
    assert "Chapters detected" in line
    assert "3" in line


def test_chapter_clip_extracted():
    line = format_event(_e("ChapterClipExtracted", chapter_index=0, clip_path="/tmp/c.mp4"), ts=TS)
    assert "Chapter 1" in line
    assert "clip extracted" in line
    assert "/tmp/c.mp4" in line


def test_chapter_transcribed():
    line = format_event(_e("ChapterTranscribed", chapter_index=1, text="hello world foo"), ts=TS)
    assert "Chapter 2" in line
    assert "transcribed" in line
    assert "3" in line  # word count


def test_captions_generated():
    line = format_event(_e("CaptionsGenerated", chapter_index=0, format="srt"), ts=TS)
    assert "Chapter 1" in line
    assert "captions generated" in line
    assert "srt" in line


def test_subtitle_image_rendered():
    line = format_event(_e("SubtitleImageRendered", chapter_index=0, image_paths=["a.png", "b.png"]), ts=TS)
    assert "Chapter 1" in line
    assert "subtitles rendered" in line
    assert "2" in line


def test_clip_rendered():
    line = format_event(_e("ClipRendered", chapter_index=2, output_path="/out/clip.mp4"), ts=TS)
    assert "Chapter 3" in line
    assert "clip rendered" in line
    assert "/out/clip.mp4" in line


def test_job_completed():
    line = format_event(_e("JobCompleted", output_paths=["a.mp4", "b.mp4"]), ts=TS)
    assert "Job completed" in line
    assert "2" in line


def test_job_failed_shows_error_text():
    line = format_event(_e("JobFailed", error="transcription timed out"), ts=TS)
    assert "Job failed" in line
    assert "transcription timed out" in line


def test_unknown_event_falls_back_to_json():
    line = format_event({"type": "SomeNewEvent", "payload": {"foo": "bar"}}, ts=TS)
    assert "SomeNewEvent" in line
    assert "foo" in line
    assert "bar" in line
