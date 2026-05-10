from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


JobStatus = Literal["pending", "running", "completed", "failed"]


class PipelineOptions(BaseModel):
    transcription: bool = True
    captions: bool = True
    render: bool = True
    segment_proposer: bool = True
    reframe: bool = True
    broll: bool = True
    thumbnail: bool = True

    # W1.10 — custom clip length range (seconds). When None, fall back
    # to settings.target_clip_seconds_{min,max}.
    target_length_min_seconds: int | None = None
    target_length_max_seconds: int | None = None

    # W1.7 — AI hook stage (opt-in).
    ai_hook: bool = False
    # W1.8 — speech enhancement stage (additive; default on).
    audio_enhance: bool = True


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
    source: str | None = None  # platform_id derived at job creation
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
    segment_mode: Literal["auto", "chapter"] = "auto"
    language: str = "en-US"
    prompt: str | None = None
    auto_hook: bool = True
    brand_template_id: str | None = None
    pipeline_options: PipelineOptions = Field(default_factory=PipelineOptions)
