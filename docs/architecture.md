# Reelsmith Architecture

## Event Flow

```
POST /jobs
    │
    ▼
job_queue (asyncio.Queue)
    │  (max_concurrent_jobs semaphore)
    ▼
AsyncEventBus.publish(VIDEO_REQUESTED)
    │
    ▼
Orchestrator
    ├─ FOLDER_CREATED
    ├─ VIDEO_DOWNLOADED
    ├─ CHAPTERS_DETECTED
    ├─ [per chapter/segment fan-out]
    │   ├─ CHAPTER_CLIP_EXTRACTED
    │   ├─ CHAPTER_TRANSCRIBED
    │   ├─ CAPTIONS_GENERATED
    │   ├─ SUBTITLE_IMAGE_RENDERED
    │   ├─ CLIP_RENDERED
    │   └─ THUMBNAIL_GENERATED
    ├─ SEGMENTS_PROPOSED  (when segment_provider != "chapter")
    ├─ SEGMENT_SCORED
    ├─ JOB_COMPLETED
    └─ JOB_FAILED

React SSE: GET /jobs/:id/events → EventSource streams events above
```

## Provider Plug-points

| Feature | Setting | Values |
|---|---|---|
| Transcription | `YTVIDEO_TRANSCRIPTION_PROVIDER` | `whisper`, `stub` |
| Segment scoring | `YTVIDEO_SEGMENT_PROVIDER` | `chapter`, `local_heuristic`, `stub` |
| Reframe | `YTVIDEO_REFRAME_PROVIDER` | `letterbox`, `face_track`, `stub` |
| B-Roll | `YTVIDEO_BROLL_PROVIDER` | `none`, `local` |
| Job store | `YTVIDEO_JOB_STORE` | `memory`, `sql` |

All providers follow the same pattern: `get_<feature>_service()` factory reads the setting and returns a Protocol implementation. Adding a new provider only requires implementing the Protocol and registering in the factory.

## Key Services

| Service | Responsibility |
|---|---|
| `download_service` | yt-dlp download, chapter extraction |
| `clip_service` | FFmpeg clip extraction, subtitle overlay |
| `transcription_service` | Whisper word-level timing |
| `caption_service` | SRT/WebVTT generation from word timings |
| `subtitle_image_service` | Per-caption PNG rendering |
| `render_service` | Final vertical-format MP4 via MoviePy |
| `thumbnail_service` | JPEG thumbnail from clip midpoint |
| `segment_proposer` | Virality scoring + segment selection |
| `reframe_service` | Face-tracked crop track |
| `broll_service` | Noun-phrase → local clip lookup |
