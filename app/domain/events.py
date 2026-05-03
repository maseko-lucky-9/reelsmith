from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.domain.ids import new_event_id


class EventType(str, Enum):
    VIDEO_REQUESTED = "VideoRequested"
    FOLDER_CREATED = "FolderCreated"
    VIDEO_DOWNLOADED = "VideoDownloaded"
    CHAPTERS_DETECTED = "ChaptersDetected"
    CHAPTER_CLIP_EXTRACTED = "ChapterClipExtracted"
    CHAPTER_TRANSCRIBED = "ChapterTranscribed"
    CAPTIONS_GENERATED = "CaptionsGenerated"
    SUBTITLE_IMAGE_RENDERED = "SubtitleImageRendered"
    CLIP_RENDERED = "ClipRendered"
    THUMBNAIL_GENERATED = "ThumbnailGenerated"
    SEGMENTS_PROPOSED = "SegmentsProposed"
    SEGMENT_SCORED = "SegmentScored"
    UPLOAD_RECEIVED = "UploadReceived"
    SOCIAL_CONTENT_GENERATED = "SocialContentGenerated"
    EXPORT_COMPLETED = "ExportCompleted"
    MANIFEST_CREATED = "ManifestCreated"
    JOB_COMPLETED = "JobCompleted"
    JOB_FAILED = "JobFailed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Event:
    type: EventType
    job_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=new_event_id)
    occurred_at: datetime = field(default_factory=_now)
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if self.correlation_id is None:
            self.correlation_id = self.job_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type.value,
            "job_id": self.job_id,
            "correlation_id": self.correlation_id,
            "occurred_at": self.occurred_at.isoformat(),
            "payload": self.payload,
        }
