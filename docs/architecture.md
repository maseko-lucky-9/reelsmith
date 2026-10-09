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

Two optional inputs extend the same graph (FR-010): `broll` is passed by the orchestrator's B-roll step (below); `crop_track` is passed by the face-tracked reframe step below. `crop_track` pans a canvas-aspect crop of the source instead of the letterboxed inset. `broll` (up to four `BrollInsert`s) adds one looped input per insert, cover-fits it to the canvas and overlays it full-canvas over its half-open window, above the inset composite and below the captions. Frame grid, duration and audio stay those of the render without them (ADR-004, B-roll addendum).

**Face-tracked reframe** (FR-010, [ADR-006](decisions/006-face-track-reframe.md)). With the job's `reframe` option on and `YTVIDEO_REFRAME_PROVIDER=face_track` (default `letterbox`), `orchestrator._reframe_step` runs `reframe_service.face_track` in a worker thread (`to_thread_cancellable`) just before the render, for new renders and re-renders alike. It decodes the chapter with PyAV at 2 fps (rotation applied, fitted into 640x640), finds faces with YuNet on onnxruntime (`face_detector`; the 232 KB model is downloaded on first use into `YTVIDEO_REFRAME_MODEL_DIR` and checked against a pinned SHA-256), follows the largest face with a zero-phase-smoothed, dead-zoned, speed-capped crop position and passes at most 64 keyframes to `render_clip(crop_track=...)`, which pans a full-height 9:16 window instead of the letterboxed inset. A split screen, several faces of similar size, no face, a source with no pan room or any error emits `StageSkipped(reframe, reason)` and leaves the letterbox render unchanged; cancellation propagates.

## B-roll

`_broll_step` (`app/workers/orchestrator.py`) runs before each reel's render when the job's `render` and `broll` options are on. With `YTVIDEO_BROLL_PROVIDER=none` (the default) it emits `StageSkipped(broll, reason="no provider")` and the render is unchanged. Otherwise:

- **Plan.** `broll_planner.plan_broll(words, duration)` is pure and deterministic: at most two 3 s windows on the clip's own clock, none starting before 3.0 s or ending after `duration - 2.0` s, at least 1 s apart. Each window's query is the longest token spoken inside it (lowercase, at least 4 letters, letters only, not one of the segment proposer's stopwords); windows are ranked by query length, then earliest start, one window per query. A clip under 8 s gets none.
- **Fetch.** `broll_service.fetch_all` asks the provider for each query, two at a time on the event loop. `local` matches keyword-named `*.mp4` files in `YTVIDEO_BROLL_LIBRARY_DIR`; `pexels` (`broll_pexels_service`) searches Pexels videos with `YTVIDEO_PEXELS_API_KEY`, downloads at most 50 MB from `*.pexels.com` hosts only and caches by Pexels video id in `YTVIDEO_BROLL_CACHE_DIR`. A failed or empty query, or a file that does not decode, drops that one insert.
- **Render and record.** Found assets become `BrollInsert`s for `render_clip(broll=...)`; `BRollApplied` reports them and the clip's `broll_assets` keeps `query, start, duration, provider, asset_id, author, source_url, path`. The export manifest's `broll_credits` column credits each asset (Pexels asks for the videographer and a link).
- **Never fatal.** Any error, or nothing found, emits `StageSkipped(broll, reason)` and the reel renders without B-roll; cancellation propagates. A re-render goes through the same step, so it refreshes (or clears) `broll_assets`.

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
| Reframe | `YTVIDEO_REFRAME_PROVIDER` | `letterbox` (default), `face_track`; any other value is `letterbox` |
| B-Roll | `YTVIDEO_BROLL_PROVIDER` | `none` (default), `local`, `pexels` |
| Job store | `YTVIDEO_JOB_STORE` | `memory`, `sql` |

All providers follow the same pattern: `get_<feature>_service()` factory reads the setting and returns a Protocol implementation. Adding a new provider only requires implementing the Protocol and registering in the factory. Reframe differs: `orchestrator._reframe_step` reads the setting, and the face detector behind the `FaceDetector` protocol comes from `face_detector.get_face_detector()`.

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
| `reframe_service` | Face-tracked crop track: 2 fps PyAV sampling, primary-face choice, split-screen / similar-faces fallbacks, smoothing, keyframe decimation |
| `face_detector` | `FaceDetector` protocol; YuNet 2023mar on onnxruntime with own pre/post-processing; lazy, SHA-256-verified model download |
| `broll_planner` | B-roll windows and their one-word queries from a clip's words (pure) |
| `broll_service` | B-roll providers (`local` keyword-named library, `pexels`) and the bounded fetch |
| `broll_pexels_service` | Pexels video search + guarded, cached download (credit: author + page URL) |
