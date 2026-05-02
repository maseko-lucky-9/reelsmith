from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


JobStatus = Literal["pending", "running", "completed", "failed"]
ChapterStatus = Literal[
    "pending",
    "extracting",
    "transcribing",
    "captioning",
    "rendering",
    "completed",
    "failed",
]


class Chapter(BaseModel):
    index: int
    title: str
    start: float
    end: float


class ChapterArtifacts(BaseModel):
    chapter_index: int
    status: ChapterStatus = "pending"
    clip_path: str | None = None
    audio_path: str | None = None
    transcript: str | None = None
    captions_path: str | None = None
    image_paths: list[str] = Field(default_factory=list)
    output_path: str | None = None
    error: str | None = None


class JobState(BaseModel):
    job_id: str
    status: JobStatus = "pending"
    current_step: str | None = None
    url: str
    download_path: str
    caption_format: str = "srt"
    target_aspect_ratio: float = 9 / 16
    destination_folder: str | None = None
    clips_folder: str | None = None
    video_path: str | None = None
    title: str | None = None
    duration: float | None = None
    chapters: dict[int, ChapterArtifacts] = Field(default_factory=dict)
    output_paths: list[str] = Field(default_factory=list)
    error: str | None = None
    clips: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)
