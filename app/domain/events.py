from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.domain.ids import new_event_id

if TYPE_CHECKING:  # pragma: no cover - import only for typing
    from app.bus.event_bus import AsyncEventBus

_log = logging.getLogger(__name__)


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
    STAGE_SKIPPED = "StageSkipped"
    JOB_COMPLETED = "JobCompleted"
    JOB_FAILED = "JobFailed"
    # ── Parity Wave (W1-W3) service emits ────────────────────────────────────
    AUDIO_ENHANCED = "AudioEnhanced"
    AI_HOOK_GENERATED = "AiHookGenerated"
    BROLL_APPLIED = "BRollApplied"
    XML_EXPORTED = "XmlExported"
    PUBLISH_QUEUED = "PublishQueued"
    PUBLISH_COMPLETED = "PublishCompleted"
    PUBLISH_FAILED = "PublishFailed"
    TIMELINE_EDITED = "TimelineEdited"
    TIMELINE_RENDERED = "TimelineRendered"
    FILLERS_REMOVED = "FillersRemoved"
    VOICEOVER_GENERATED = "VoiceoverGenerated"
    ANIMATED_CAPTION_RENDERED = "AnimatedCaptionRendered"
    TRANSITIONS_APPLIED = "TransitionsApplied"
    BRAND_VOCAB_APPLIED = "BrandVocabApplied"
    SCHEDULED_POST_QUEUED = "ScheduledPostQueued"
    WEBHOOK_DISPATCHED = "WebhookDispatched"
    BULK_EXPORT_COMPLETED = "BulkExportCompleted"
    SHARE_LINK_CREATED = "ShareLinkCreated"
    ANALYTICS_REFRESHED = "AnalyticsRefreshed"


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


def emit_from_sync(
    bus: "AsyncEventBus | None",
    job_id: str | None,
    event_type: EventType,
    payload: dict[str, Any] | None = None,
) -> bool:
    """Best-effort emit helper for sync services that may be called inside an
    already-running asyncio loop.

    Returns True if a publish was scheduled, False otherwise. Silently no-ops
    when:
      - ``bus`` or ``job_id`` is None (caller opted out / legacy path), or
      - the current thread has no running event loop (e.g. sync function
        invoked via ``asyncio.to_thread``). In that case the orchestrator or
        router-side wrapper is responsible for emitting.

    The scheduled task is fire-and-forget; tests should ``await
    asyncio.sleep(0)`` (or use ``asyncio.wait_for`` on a subscriber) to let
    the publish coroutine drain before asserting.
    """
    if bus is None or job_id is None:
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop in this thread — caller (orchestrator) emits instead.
        return False
    loop.create_task(
        bus.publish(Event(type=event_type, job_id=job_id, payload=payload or {}))
    )
    return True
