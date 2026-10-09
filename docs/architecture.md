# Reelsmith Architecture

## Event Flow

```
POST /jobs   (uploads, generate and clip-rerender routers enqueue the same way)
    │
    ▼
job_queue (asyncio.Queue) → queue worker → AsyncEventBus.publish(VIDEO_REQUESTED)
    │
    ▼
run_orchestrator  (max_concurrent_jobs semaphore around each _run_job;
    │              jobs waiting for a slot stay "pending")
    ├─ FOLDER_CREATED
    ├─ VIDEO_DOWNLOADED
    ├─ CHAPTERS_DETECTED
    ├─ [per chapter/segment fan-out: asyncio.TaskGroup, max_parallel_chapters;
    │    first failing chapter cancels the rest (their ffmpeg is killed)
    │    → JOB_FAILED is emitted once, last]
    │   ├─ CHAPTER_CLIP_EXTRACTED  (chapter audio only; clip_path=None)
    │   ├─ AUDIO_ENHANCED
    │   ├─ CHAPTER_TRANSCRIBED
    │   ├─ FILLERS_REMOVED
    │   ├─ CAPTIONS_GENERATED
    │   ├─ CLIP_RENDERED
    │   ├─ THUMBNAIL_GENERATED
    │   ├─ AI_HOOK_GENERATED
    │   └─ SOCIAL_CONTENT_GENERATED
    ├─ EXPORT_COMPLETED
    ├─ MANIFEST_CREATED
    ├─ JOB_COMPLETED
    └─ JOB_FAILED
    (STAGE_SKIPPED for each stage turned off in PipelineOptions)

React SSE: GET /jobs/:id/events → EventSource streams events above
```

## Reprompt

`POST /jobs/{id}/reprompt` (completed job, source still on disk) queues `{reprompt: true, prompt, length range or start/end}` like a clip re-render; `_run_job` hands it to `_reprompt_job`, which:

- forgets the job's replay history (`AsyncEventBus.forget`), so a new SSE stream is not closed by the previous run's `JOB_COMPLETED`; the router does this too when it accepts;
- reuses the source's `.words.json` sidecar (or transcribes the source once), then proposes with the prompt and length range (discovery's `select_discovered`), or takes the one requested time range;
- renders the new clips hidden (`retired`), numbered after every existing clip file, exports them and rewrites the manifest;
- only then puts them live, retires the old clips, records the prompt and length range, and emits `JOB_REPROMPTED` + `JOB_COMPLETED`.

The job stays `completed` throughout, so a restart (`fail_interrupted_jobs`) never fails it and its URL still dedups to it. Any failure keeps the old clips, drops the new ones and emits `REPROMPT_FAILED` (never `JOB_FAILED`). One reprompt per job at a time (an in-process set in the orchestrator; a second request gets 409).

## Routing

The API is served at both `/x` and `/api/x`. Routers are mounted without a prefix, and `ApiPrefixMiddleware` (`app/api_prefix.py`) strips one leading `/api` segment before routing (`/api` alone becomes `/`; `/apixyz` is not rewritten). The React client calls `/api/...`: in dev the Vite proxy strips the prefix, and with `YTVIDEO_SERVE_FRONTEND=true` the middleware does, while `StaticFiles` serves the built UI at `/`. The middleware is pure ASGI: it rewrites `path` and `raw_path`, keeps `root_path`, and passes `receive`/`send` through, so SSE and streamed downloads are unaffected and the app-level API-key dependency applies the same at both addresses. See [ADR-005](decisions/005-api-route-prefix.md).

## Concurrency and Recovery

- **Job cap.** `run_orchestrator` (`app/workers/orchestrator.py`) wraps each `_run_job` in a `max_concurrent_jobs` semaphore (values below 1 are clamped to 1 with a warning). On shutdown it waits for every running job to unwind.
- **Chapter fan-out.** Chapters run in an `asyncio.TaskGroup`, bounded by `max_parallel_chapters` (default 1). Errors from other chapters are logged.
- **Cancellable blocking work.** ffmpeg and Whisper run in worker threads through `ffmpeg_tools.to_thread_cancellable`. A timeout or cancel kills the ffmpeg child, and Whisper checks a cancel event between segments.
- **Restart recovery.** At startup with the SQL store, `fail_interrupted_jobs()` marks jobs left `pending`/`running` as `failed` ("interrupted by restart"), so the duplicate-URL check no longer blocks their URL. It runs before the queue worker starts.

## Render Pipeline

`render_service.render_clip` renders each chapter in **one ffmpeg pass straight from the source**: trim, blurred background still, scaled inset, caption overlay, even-dimension crop, then yuv420p libx264/AAC. The ffmpeg binary comes from `imageio-ffmpeg`, and probes and frame grabs use PyAV (`ffmpeg_tools`). Captions are drawn by the unchanged PIL renderer, once per unique caption, and composited as a single ffconcat overlay input (`caption_track`). See [ADR-004](decisions/004-ffmpeg-render-pipeline.md) for the timing rules and the deliberate behaviour changes.

Two optional inputs extend the same graph; no pipeline caller passes either yet (FR-010). `crop_track` pans a canvas-aspect crop of the source instead of the letterboxed inset. `broll` (up to four `BrollInsert`s) adds one looped input per insert, cover-fits it to the canvas and overlays it full-canvas over its half-open window, above the inset composite and below the captions. Frame grid, duration and audio stay those of the render without them (ADR-004, B-roll addendum).

## Live Progress (SSE)

- **Backend.** The backend sends **named** SSE events (`event: <EventType>`, `id: <event_id>`). The event bus keeps its last 200 events and replays this job's events from that history to each new subscriber.
- **Web.** `web/src/hooks/useJobSSE.ts` registers a listener for every backend event type (a drift test parses `app/domain/events.py`) and dedupes events by `event_id`. It refreshes only `['job', id]` and `['clips', id]` (bursts merged within 500 ms), and `['jobs']` only on terminal events (`JobCompleted`, `JobFailed`, `RepromptFailed`; the backend ends the stream on the same three). The job page does not poll, and its stream is open only while the job is `pending`/`running` or a reprompt it queued is running. A fallback poll runs only when SSE fails.

## Bulk Export

`GET /clips/bulk-export.zip` builds a stored (uncompressed) zip in a worker thread into an anonymous temp file. It streams the file in 1 MiB chunks with an exact `Content-Length`, and closes the handle when the stream ends, fails or the client disconnects.

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
| `clip_service` | Caption schedule, chapter audio extraction (ffmpeg), background still |
| `transcription_service` | faster-whisper word-level timing (locked lazy model load, optional start-up warm-up, cancellable decode) |
| `caption_service` | SRT/WebVTT generation from word timings |
| `subtitle_image_service` | Per-caption PNG rendering |
| `caption_track` | Unique caption PNGs cropped to one even rect, plus the ffconcat list for the render |
| `ffmpeg_tools` | Bundled-ffmpeg runner (kills the child on timeout/cancel), PyAV probes and frame grab |
| `render_service` | Final vertical-format MP4 in one ffmpeg pass from the source |
| `thumbnail_service` | JPEG thumbnail from clip midpoint |
| `segment_proposer` | Heuristic segment scoring + selection; picks the clips of a source without chapters when `YTVIDEO_SEGMENT_PROVIDER` is not `chapter` (default `chapter`: one Full Video clip) |
| `segment_discovery` | Discovery helpers: words rebased to a clip window, `<stem>.words.json` sidecar, selection by relative score bar (60% of best), length-scaled clip budget (1 per 120 s, max 5), 50% coverage cap and a near-duplicate penalty (shared transcript words with a kept clip) |
| `reframe_service` | Face-tracked crop track |
| `broll_service` | Noun-phrase → local clip lookup |
