# Feature Specification: Reelsmith Baseline (inventory of current behaviour)

**Feature Branch**: `n/a (baseline inventory; not a feature branch)`
**Created**: 2026-10-09
**Status**: Accepted 2026-10-09 (owner sign-off)
**Input**: Retro-specification of the product as it exists at `main` `40ab44d`, derived from the code, tests, ADR-001…004 and the wave gate documents.
**Updated**: 2026-10-09, in place, to `main` `67dd68d` (PR #54): every row was re-checked against the code; see *Changes since baseline* at the end.

> **How to read this.** This is an *inventory*, not a proposal. Spec Kit normally specifies one change; here the inventory is the deliverable (it gives later `specs/002-…` a baseline to extend). Every requirement carries a status tag:
>
> | Tag | Meaning |
> |---|---|
> | `Implemented` | Reachable behaviour with a named test file. The test's strength is **not** audited unless the *Audit notes* say so. |
> | `Untested` | Reachable (verified by probe or reading), but no test covers it. |
> | `Partial` | Reachable, but a verified defect makes part of it wrong. |
> | `Scaffolded-unwired` | Code, table or UI exists but nothing in the running app uses it. |
> | `Missing` | Referenced by the UI or docs, no backend. |
>
> Counts below were produced by commands at `67dd68d`, not recalled: 46 HTTP operations on 38 paths (`create_app().openapi()["paths"]`), 40 `EventType` members (38 at `40ab44d`; reprompt added `JobReprompted` and `RepromptFailed`), 16 ORM tables (`Base.metadata.tables`), 17 Alembic revisions (`ls alembic/versions`; 16 at `40ab44d`, the re-render fix added `o3p4q5r6s7t8`).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Turn a video URL into captioned vertical clips (Priority: P1)

A creator pastes a YouTube, TikTok, Instagram or Facebook URL, picks pipeline options, and gets one short vertical clip per detected chapter, each with burned-in word-synced captions and a thumbnail, with live progress.

**Why this priority**: this is the product. Everything else operates on the clips it produces.

**Independent Test**: `POST /jobs` with a fixture URL, follow `GET /jobs/{id}/events`, then `GET /clips?job_id=…` and fetch `GET /clips/{id}/video`.

**Acceptance Scenarios**:

1. **Given** a supported URL, **When** `POST /jobs`, **Then** the response is 202 with `job_id` and status `accepted` (a duplicate returns the existing job's status), and the orchestrator picks it up.
2. **Given** a job for the same URL is already pending, running or completed, **When** `POST /jobs` repeats the URL, **Then** the existing job id is returned and the new options are ignored.
3. **Given** an unsupported platform, **When** `POST /jobs`, **Then** the response is 400 with the URL echoed.
4. **Given** a source with chapters, **When** the job runs, **Then** one clip is rendered per chapter, with captions burned in, as yuv420p with even dimensions.
5. **Given** a source without chapters, **When** the job runs, **Then** a single "Full Video" chapter covers the whole probed duration (default `segment_provider=chapter`). With `local_heuristic` the best-scoring windows become the clips instead, and any failure falls back to the single chapter (FR-009, opt-in).
6. **Given** a running job, **When** a client opens the SSE stream, **Then** it receives that job's events in order, and finishes on `JobCompleted` or `JobFailed`.
7. **Given** the server restarts mid-job, **When** it starts, **Then** interrupted jobs are marked failed rather than left "running".

### User Story 2 - Bring your own footage, or generate it (Priority: P2)

A creator uploads an MP4, or submits a text brief to generate a video, and the same pipeline runs on it.

**Why this priority**: it removes the dependence on a downloadable URL.

**Independent Test**: `POST /uploads` with a small MP4, and `POST /generate` with `YTVIDEO_GENERATE_ENABLED=true`.

**Acceptance Scenarios**:

1. **Given** an MP4 under the size limit, **When** `POST /uploads`, **Then** a job on an `upload://` URL is enqueued.
2. **Given** a non-MP4 type, **Then** 415. **Given** a file over `max_upload_mb` (500), **Then** 413.
3. **Given** generate mode is disabled, **When** `POST /generate`, **Then** 400. **Given** it is enabled, **Then** 202 and a brief is written for a `generate://` job.

### User Story 3 - Review, rate and re-run clips (Priority: P3)

A creator browses clips, filters them, likes or dislikes them, and asks for a re-render or a re-prompt with a different length range.

**Why this priority**: it is how a creator curates the output.

**Independent Test**: `GET /clips?job_id=&min_score=&search=`, `PATCH /clips/{id}/like`, `POST /clips/{id}/rerender`, `POST /jobs/{id}/reprompt`.

**Acceptance Scenarios**:

1. **Given** clips exist, **When** listed with `job_id`, `min_score` or `search`, **Then** only matching, non-retired clips are returned.
2. **Given** a clip, **When** `PATCH /clips/{id}/like`, **Then** the response says liked **and the change persists on the next read** (FR-014, fixed).
3. **Given** a clip, **When** `POST /clips/{id}/rerender`, **Then** that clip is re-rendered in place from the job's saved source video and the job stays `completed`. A job with no saved source (created before `jobs.video_path`), or one not yet completed, gets 409 (FR-015, fixed).
4. **Given** a completed job and clip discovery on (`segment_provider` not `chapter`, FR-009), **When** `POST /jobs/{id}/reprompt` with a prompt and a length range, **Then** the segment proposer re-runs with that prompt, the new clips replace the old ones once all of them have rendered, and the job stays `completed`; a failed reprompt keeps the old clips (FR-016, fixed). With discovery off, only a reprompt with an explicit `start_seconds`/`end_seconds` range runs; any other gets 409.

### User Story 4 - Edit a clip on a timeline (Priority: P4)

A creator opens a clip in a multi-track editor, saves a timeline, and gets a render plan for it. Nothing renders a saved timeline into a video yet; the editor's "Regenerate captions" button re-renders the clip from its source (FR-015). They can also add an AI hook line and enhance the audio.

**Why this priority**: differentiation from a plain clipper (ADR-003 Wave 1).

**Independent Test**: `PUT /api/clips/{id}/edit`, `GET …/edit/plan`, `POST …/ai-hook`, `POST …/enhance-audio`.

**Acceptance Scenarios**:

1. **Given** a clip, **When** a timeline is saved with `PUT`, **Then** `GET` returns it with an incremented version, and `DELETE` removes it.
2. **Given** a saved timeline, **When** `GET …/edit/plan`, **Then** a render plan is returned.
3. **Given** a clip with a transcript, **When** `POST …/ai-hook`, **Then** hook text is generated by the local Ollama model, truncated to `ai_hook_max_chars` (80); on any failure an empty string is returned, with no fallback text.
4. **Given** `provider` of `loudnorm`, `rnnoise` or `passthrough`, **When** `POST …/enhance-audio`, **Then** 202; an unknown provider gets 422 listing the allowed values.

### User Story 5 - Apply a brand (Priority: P5)

A creator manages brand templates (logo, font, colours, caption style) and attaches one to a job. The job stores the template id, but no render applies a template yet (FR-023), and the template API does not expose the `vocabulary` column.

**Independent Test**: `POST /brand-templates`, `POST /brand-templates/{id}/assets`, then submit a job with `brand_template_id`.

**Acceptance Scenarios**:

1. **Given** valid input, **When** a template is created, read, updated and deleted, **Then** each call returns the expected status.
2. **Given** a logo or font file, **When** uploaded to a template, **Then** it is stored and referenced by the template.

### User Story 6 - Publish to social platforms (Priority: P6)

A creator connects accounts and publishes a clip now. Scheduled publishing was removed (FR-032).

**Independent Test**: `POST /social/accounts`, `POST /social/publish`, `GET /social/jobs?status=`.

**Acceptance Scenarios**:

1. **Given** a connected account and a clip, **When** `POST /social/publish`, **Then** a `queued` publish job runs in the background and its status is readable.
2. **Given** a body with `schedule_at` (or any other unknown field), **When** `POST /social/publish`, **Then** 422 and no publish job is created (FR-032 removed).
3. **Given** unknown clip or account ids, **Then** 404.
4. **Given** TikTok, **When** `POST /social/tiktok/connect` stores a cookie session, **Then** `GET /social/tiktok/capabilities` reports what it can do.

### User Story 7 - Export clips (Priority: P7)

A creator exports a clip for Premiere or DaVinci, or downloads many clips with a manifest.

**Independent Test**: `GET /api/clips/{id}/export.xml?format=premiere`, `GET /api/clips/bulk-export.zip`.

**Acceptance Scenarios**:

1. **Given** a rendered clip, **When** requesting `export.xml`, **Then** NLE XML is returned; for an unrendered clip, 409.
2. **Given** selected clips, **When** requesting the bulk zip, **Then** a zip with a manifest is streamed; more than `bulk_export_max_clips` (200) ids is rejected with 422.
3. **Given** a retired clip, **When** bulk-exporting, **Then** it is absent from the manifest and the zip; if every requested clip is retired the response is 404 (FR-051, fixed).

### Edge Cases

- Source shorter than the target clip range, or with no chapters (single full-video chapter).
- Variable-frame-rate sources: renders are constant frame rate at the stream's average rate (ADR-004).
- Odd source dimensions: output is cropped to even width and height so the file plays everywhere (ADR-004).
- Whisper unavailable: `YTVIDEO_TRANSCRIPTION_PROVIDER=stub` keeps the pipeline runnable.
- A stage fails inside one chapter: the chapter task is bounded by `max_parallel_chapters`; the job status reflects failure through `JobFailed`. Clip discovery, face-tracked reframe and B-roll never fail a job or a chapter: an error falls back (one "Full Video" chapter, the letterbox, no inserts), with `StageSkipped(<stage>, reason)` except when discovery simply keeps no segment.
- Auth: with `YTVIDEO_REQUIRE_AUTH=true`, every API route requires the API key, the docs routes (`/docs`, `/redoc`, `/openapi.json`, also under `/api`) are switched off and answer 404 (T043), and the static frontend mount stays open (FR-060).
- Output folders: each job writes to `<download_path>/<slug>-<job_id[:8]>/clips` (slug: the video title, or `upload_video` / `generate_video` / `<platform>_video`), so two jobs never share a clip path (T030). Folders made before T030 are named `<slug>/clips`; re-render still finds them because it works from the clip's own `output_path`. `POST /folders` has no job and keeps the bare slug.
- Storage: `YTVIDEO_DEFAULT_DOWNLOAD_PATH` defaults to `<project>/data/downloads` (gitignored), uploads live in its `uploads/` subfolder (T031). `POST /jobs` uses it when the request has no `download_path`; the UI sends none (T035).
- Route prefixes: every API route answers at both `/x` and `/api/x` (T034, ADR-005). Routers are unprefixed and `ApiPrefixMiddleware` strips a leading `/api` segment, so the dev proxy (which strips `/api`) and `serve_frontend` (which does not) reach the same routes. Paths in this spec are written either way.

## Requirements *(mandatory)*

### Functional Requirements

**Ingest and pipeline**

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-001 | The system MUST accept a URL via `POST /jobs`, dedupe by URL, and reject unsupported platforms with 400. | Implemented (the 400 returns `{"detail": "Unsupported platform for URL: <url>"}` and creates no job; T027. `download_path` is optional and defaults to `YTVIDEO_DEFAULT_DOWNLOAD_PATH`; T035) | `tests/contract/test_jobs_router.py`, `test_store_lookup_routes.py`, `test_jobs_download_path_default.py` |
| FR-002 | The system MUST support YouTube, TikTok, Instagram, Facebook, upload and generate sources through platform adapters. | Implemented | `tests/unit/test_platform_adapters.py`, `test_platform_registry.py` |
| FR-003 | The system MUST stream per-job events over SSE. | Implemented | `tests/contract/test_generate_pipeline.py`, `tests/e2e/test_happy_path.py`; keep-alive pings every `YTVIDEO_SSE_KEEPALIVE_SECONDS`: `tests/unit/test_sse_keepalive.py` |
| FR-004 | The system MUST run at most `max_concurrent_jobs` jobs and `max_parallel_chapters` chapters at once. | Implemented | `tests/unit/test_orchestrator_concurrency.py` (the 4 chapter-failure tests are skipped below Python 3.13, T019: 3.12's `TaskGroup` leaves `cancelling()==1`; CI is 3.14 only) |
| FR-005 | The system MUST mark interrupted jobs failed at startup. | Implemented | `tests/unit/test_startup_recovery.py` |
| FR-006 | The system MUST transcribe with word-level timings (faster-whisper) or a stub. | Implemented | `tests/unit/test_transcription_service.py`, `test_transcription_whisper_path.py` |
| FR-007 | The system MUST render each chapter in one ffmpeg pass, to yuv420p with even dimensions, captions pixel-identical to the PIL renderer. | Implemented | `tests/unit/test_render_service.py`, `test_caption_track.py`, `tests/sync_checker.py` |
| FR-008 | The system MUST generate a thumbnail per rendered clip and, optionally, AI hook text, filler removal and audio enhancement. | Implemented | `tests/unit/test_thumbnail_service.py`, `test_ai_hook_service.py`, `test_filler_transition_profanity.py`, `test_audio_enhance_service.py`; the pipeline emits `AudioEnhanced`, `FillersRemoved` and `AiHookGenerated` per chapter: `test_orchestrator_stage_events.py` |
| FR-009 | The system MUST propose and score segments for sources without chapters. | Implemented (opt-in: `YTVIDEO_SEGMENT_PROVIDER=local_heuristic`; the default `chapter`, kept by owner choice (T011), gives a source without chapters one "Full Video" clip; clip quality is not signed off, gate G1) | `_discover_segments` (`app/workers/orchestrator.py:379`) runs when the source has no chapters, the job's `segment_proposer` and `transcription` options are on, `segment_mode` is `auto` and the provider is not `chapter` (`_discovery_enabled`, `:364`; `stub` also turns it on and returns one fixed 0-30 s segment, for tests). It transcribes the whole source once, writes `<source stem>.words.json` next to the source, scores windows with `get_segment_proposer()` (job clip length range, prompt, wav RMS; `LocalHeuristicProposer` is numpy + stdlib only) and keeps highlights, not slices (`select_discovered`, `app/services/segment_discovery.py:184`): the clip budget scales with source length (one clip per 120 s, rounded half up, 1 to 5), the kept clips cover at most 50% of the source (the best is always kept), and a segment must score at least 60% of the best (heuristic scores run about 13-38 on a real talk, so no fixed bar) after a near-duplicate penalty: picked greedily by `score * (1 - redundancy)`, redundancy being the largest share of its transcript content words already in one kept clip, and skipped at 60% or more; non-overlapping, touching allowed. Each becomes a chapter that reuses the full-source words rebased onto its window; the clip stores `virality_score`, `score_breakdown` and the proposer summary; `SegmentsProposed` and `SegmentScored` are emitted. A short source, no kept segment or any discovery failure keeps the single "Full Video" chapter. A single-clip re-render reuses the sidecar words. Optional re-rank (T040; `YTVIDEO_SEGMENT_RERANK_PROVIDER=ollama`, default `none`): before the selection, `segment_rerank.rerank` sends the heuristic's distinct candidates above the bar (at most 10, at most 600 UTF-8 bytes of transcript each, plus at most 200 bytes of the job prompt; bytes, so the prompt fits the 8,192-token context in any script) to the configured Ollama model in one call (thinking off, an 8,192-token context, at most 256 reply tokens), reads only finite numbers under the known candidate ids (rounded half up and clamped to 0-100) and, only when every candidate got one, blends them 50/50 with the heuristic score; the selection then runs on that shortlist and the clip's `virality_score` is the blended score. Ollama off or down, a timeout (`YTVIDEO_OLLAMA_TIMEOUT_SECONDS` bounds the call), a reply cut off by its length or one that does not score every candidate keeps the heuristic order; reprompts are not re-ranked. Smoke-tested on Ollama 0.35.1 with `qwen3:4b` (synthetic excerpts, about 1-2 s a call); no full pipeline run on a real source. Tests: `tests/unit/test_orchestrator_discover.py`, `test_segment_discovery.py`, `test_segment_rerank.py`; the proposer itself: `test_segment_proposer.py`, `test_segment_proposer_heuristic.py`, `test_segment_selection.py`. Decision record: ADR-007 (*Re-rank*). The default decision: T040. |
| FR-010 | The system MUST honour the `reframe` and `broll` pipeline options (default on). | Implemented (both opt-in): reframe when `YTVIDEO_REFRAME_PROVIDER=face_track`, B-roll when `YTVIDEO_BROLL_PROVIDER` is not `none`; the defaults (`letterbox`, `none`) leave renders unchanged | **Reframe** (PR #44 render support, PR #52 wiring, ADR-006): with the job's `reframe` option on and the provider `face_track`, `_reframe_step` (`app/workers/orchestrator.py`) runs `reframe_service.face_track` in a worker thread (`to_thread_cancellable`, cancel stops the decode) before every render, re-renders included, and passes its track to `render_service.render_clip(crop_track=...)`, which pans a canvas-aspect window instead of the letterboxed inset in the same one-pass graph (`crop_x_expr`: flat piecewise-linear sum, up to 64 keyframes). Detection: PyAV decode at 2 fps, YuNet 2023mar on onnxruntime (`app/services/face_detector.py`, model downloaded on first use into `YTVIDEO_REFRAME_MODEL_DIR`, SHA-256 pinned), one continuous primary face (another face takes over only after 1.5 s as the main face, 3 s if only ever seen small; faces from 2.5 % of the frame height count at a 0.8 score but not in the split-screen and similar-size tests; T041), zero-phase smoothing with a dead zone and a speed cap. A split screen (`active_speaker_service.detect_split_screen`), several faces of similar size, no face, a face in fewer than half the samples (slides, credits; T041), no pan room or any error emits `StageSkipped(reframe, reason)` and renders the letterbox exactly as before. With the option off or the provider `letterbox` (or any other value) nothing is decoded and the render arguments are unchanged. Tests: `tests/unit/test_reframe_service.py`, `test_face_detector.py`, `test_orchestrator_reframe.py`, `test_render_crop_track.py`, `tests/e2e/test_reframe_face_track.py` (real render keeps the tracked subject in the window), `tests/e2e/test_render_crop_track.py`, `tests/integration/test_yunet_model.py` (real model). T041 fixed three known weaknesses (cutaways, small faces in wide shots, mostly faceless clips); the remaining limits are in ADR-006 *Known limits*, and gate G2 (the owner's visual sign-off) is still open (T046). **B-roll** (PR #53 render support, PR #54 planner and providers): `orchestrator._broll_step` runs when the job's `render` and `broll` options are on. With provider `none` it emits `StageSkipped(broll, reason="no provider")` and the render is unchanged (`broll=None`, argv byte-identical). Otherwise `broll_planner.plan_broll` picks up to two 3 s windows on the clip's clock (start >= 3.0 s, end <= duration - 2.0 s, >= 1 s apart; query = longest qualifying token spoken in the window), `broll_service.fetch_all` fetches them two at a time from `local` (keyword-named mp4 in `YTVIDEO_BROLL_LIBRARY_DIR`) or `pexels` (`broll_pexels_service`: key only in the search request's `Authorization` header and never logged, downloads only from `*.pexels.com`, 50 MB cap, cache by video id), and found assets go to `render_clip(broll=[BrollInsert(path, start, duration)])`, which overlays up to 4 inserts full-canvas over half-open windows `[start, start + duration)`, each looped to cover its window and cover-fitted, between the composite (letterbox or pan) and the captions in the same one-pass graph; frame grid, duration and audio are unchanged and insert audio is never mapped (`validate_broll`). `BRollApplied` reports them and `clips.broll_assets` stores `query, start, duration, provider, asset_id, author, source_url, path` (both stores; a re-render refreshes or clears it). Credits (T039): the job's export `manifest.csv` and the bulk-export zip's `manifest.csv` both end with a `broll_credits` column (`manifest_service.broll_credits`: a JSON list with one `{provider, author, source_url}` per distinct asset, `[]` without B-roll, never a path), and the UI shows the credits (`web/src/components/broll-credits.tsx`, on the clip editor and on each job-page clip row): the author is linked to the asset page only for an absolute http(s) URL, a local file reads `Local library clip`, and any Pexels asset adds a `Videos provided by Pexels` link to https://www.pexels.com (Pexels API guidelines; its licence does not require attribution). Tests: `web/src/components/broll-credits.test.tsx`, `test_bulk_manifest_*` in `tests/contract/test_bulk_export.py`. Any failure emits `StageSkipped(broll, reason)` and renders without B-roll; cancellation propagates. Tests: `tests/unit/test_broll_planner.py`, `test_broll_service.py`, `test_broll_pexels_service.py` (`httpx.MockTransport` only), `test_orchestrator_broll.py`, `test_manifest_service.py`, `test_render_broll.py`, `tests/e2e/test_render_broll.py`. A real run recorded in PR #54 (local provider, CC0 library, Wikimania talk, whisper base) inserted 3 assets over 2 reels; a per-frame diff against the same reels rendered without B-roll changed exactly the planned frames. Pexels is covered by mocked tests only (no API key). |
| FR-011 | The system MUST accept MP4 uploads (415 wrong type, 413 too large). | Implemented | `tests/contract/test_uploads_router.py`; stored under `<default_download_path>/uploads`, the pre-T031 `/tmp/yt/uploads` root is still readable: `tests/unit/test_default_download_path.py` |
| FR-012 | The system MUST generate a video from a brief when `generate_enabled`, else 400. | Implemented (the tests fake the producer: the `stub` provider, and a monkeypatched `subprocess.run` for `ltx`; no test runs a real LTX generation, and none has been recorded) | `tests/contract/test_generate_router.py`, `test_generate_pipeline.py`, `tests/unit/test_ltx_producer.py`; operator procedure, smoke test and failure modes: [`docs/generate-stage2-runbook.md`](../../docs/generate-stage2-runbook.md) |
| FR-013 | The system MUST, on a sweep every `retention_sweep_minutes` (60): retire clips older than `retention_days` (30) and delete their video and thumbnail files; remove the source video, `.words.json` sidecar and emptied folders of a finished job that has no live clip and has been idle for `retention_days`; and delete the files of retired clips once they are older than `retired_files_grace_hours` (24). Jobs are not deleted. | Implemented (SQL store only: the lifespan janitor starts only with `YTVIDEO_JOB_STORE=sql`, the default) | `app/services/retention.py` (`run_retention_sweeps` runs `sweep_expired_clips`, `sweep_unused_sources` and `sweep_retired_files` in that order; called by the lifespan janitor in `app/main.py:137-153`); `tests/unit/test_retention_sweep.py` (T025), `tests/unit/test_retention_cleanup.py`, `tests/unit/test_retention_safe_delete.py`, `tests/unit/test_retention_races.py`, `tests/unit/test_retention_janitor.py`, `tests/contract/test_rerender_after_source_cleanup.py` (T033). Rows are changed and committed before files are deleted, and a file a live clip still references is kept (rows from before per-job folders can share a path, T030). **Sources** (T033): removed only when the job is `completed` or `failed`, has no live clip, its last activity (`updated_at`) is older than `retention_days`, neither the job row nor any of its clip rows changed in the last hour (a re-render has no in-flight registry), no reprompt is in flight, the source resolves (symlinks and `..` followed) below `default_download_path`, its `uploads/` or the legacy `/tmp/yt/uploads`, and no other job references the same file (another job's `video_path`, or the `upload://` file of a `pending`/`running` job; idle jobs sharing a source are cleaned together; the in-flight and other-job checks run after the clearing UPDATE, which is undone if one fails; the clear is a compare-and-set, so a lost race deletes nothing, and the in-flight registry is read again after the commit, right before the delete). `jobs.video_path` becomes NULL, so a re-render or reprompt answers 409 "source video not retained". Folders are removed only when empty. **Retired clip files** (T033): deleted once the file's mtime is older than the grace period, unless a live clip or a job with a reprompt in flight (its new clips are hidden as retired) references it (the clip row and live clips are re-read right before each delete), or it resolves outside those roots. **Safe deletes**: database paths with a NUL byte, a relative path or `..` are never used to delete; every delete re-validates at deletion time (`delete_below_root`: folder chain opened from the root with `O_NOFOLLOW` and `dir_fd` removal on macOS/Linux, symlinks refused). Not swept: `exports/` copies and manifests (a copy of every clip, so disk still grows by those), `_tmp` leftovers (with `exports/` they usually keep the job folder), and sources outside the roots (e.g. pre-T031 `/tmp/yt/<slug>/`). A delete that fails after the commit is not retried. |

**Clip curation**

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-014 | The system MUST persist like and dislike toggles. | Implemented (was Partial at baseline; fixed by PR #28) | Both stores now await async mutators (`app/bus/job_store.py` `_apply_clip_mutator`). Tests: `tests/unit/test_job_store_async_mutator.py` (memory and SQL), read-back tests in `tests/contract/test_store_lookup_routes.py`. Mutation-checked: removing the `await` turns 9 tests red. Before the fix a probe returned `liked=True` in the response and `None` in the store. |
| FR-015 | The system MUST re-render a single clip of a completed job in place, from its saved source video, without changing the job. | Implemented (was Partial at baseline; fixed by PR #28, follow-ups PR #48) | `POST /clips/{id}/rerender` validates (404 unknown/retired clip; 409 job missing, not completed, or source video not retained), then `orchestrator._rerender_clip` re-runs `_process_chapter` for that clip id. `jobs.video_path` (migration `o3p4q5r6s7t8`) persists the source path. Tests: `tests/contract/test_clip_rerender_router.py`, `tests/unit/test_orchestrator_rerender.py`. A real run with the bundled ffmpeg replaced the clip file (3.0 s, H.264 yuv420p), kept `liked`, created no new clip, left no temp dir and left the job `completed`. Follow-ups (T028): a failed re-render restores the chapter status (`test_failed_rerender_resets_chapter_status`); reprompt no longer persists stage overrides that stripped captions from a later re-render (`test_reprompt_does_not_persist_render_false`); `regenerate_copy=false` keeps title, summary, hashtags and AI hook text (`test_rerender_without_copy_keeps_title_summary_hashtags_and_hook`); `scripts/backfill_job_video_path.py` backfills `jobs.video_path` for older jobs where the source is still on disk (`tests/unit/test_backfill_job_video_path.py`). Limits: jobs created before the migration return 409 unless the backfill finds their source (it found 0 of 4 on the local DB: the sources had been under `/tmp`); the request's `reframe_provider` is accepted but ignored: re-renders use the server's `YTVIDEO_REFRAME_PROVIDER` like new renders (FR-010, `test_rerender_uses_the_face_track`); PostgreSQL not exercised. |
| FR-016 | The system MUST re-run segment proposal on reprompt. | Implemented (opt-in, like FR-009: was Partial at baseline; fixed by PR #51. Proposing needs `segment_provider` other than `chapter`; in the default configuration only a time-range reprompt runs and the others get 409) | Decision record: ADR-007. `POST /jobs/{id}/reprompt` (`app/routers/reprompt.py`) validates (404 unknown job; 422 bad body, inverted length range or a time range that starts past the source; 409 job not completed, source video not retained (the clip re-render rule), clip discovery off (`segment_provider=chapter`) without a time range, or a reprompt of the job already in flight) and queues `{reprompt: true, ...}`. `orchestrator._reprompt_job` (`app/workers/orchestrator.py:834`) forgets the job's SSE replay history (`AsyncEventBus.forget`, so a new stream is not closed by the old `JobCompleted`), reuses the `.words.json` sidecar (or transcribes the source once), proposes with the prompt and length range and discovery's selection (`select_discovered`) or takes the one requested time range, renders the new clips hidden and numbered after every existing clip file, exports them and rewrites the manifest, then puts them live, retires the old clips and emits `JobReprompted` + `JobCompleted`. The job stays `completed` throughout (never `running`, so `fail_interrupted_jobs` cannot fail it on a restart), and its URL still dedups to it. Any failure keeps the old clips, retires the new ones, deletes their files and emits `RepromptFailed` (never `JobFailed`; the SSE stream ends on it); only the prompt and the length range are recorded, and only on success (T028 a2). UI: a Reprompt form on `/jobs/$jobId` for completed jobs streams the job's events until the reprompt ends and shows a 409's reason. Tests: `tests/contract/test_reprompt_router.py`, `tests/unit/test_orchestrator_reprompt.py` (memory and SQL stores), `tests/unit/test_event_bus.py`, `web/src/routes/jobs.$jobId.test.tsx`, `web/src/hooks/useJobSSE.test.ts`. Real run (whisper base, `local_heuristic`, the 231 s Wikimania talk, memory store, ASGI): the reprompt "gender equality" reused the 403-word sidecar and kept the same two windows (the SDG5 gender-equality window already led; its score rose from 31 to 38, prompt feature 0.571) as new clips `02_`/`03_` and retired `00_`/`01_`; a second POST while it was queued got 409; afterwards the SSE replay started at `SegmentsProposed` and did not contain the old `JobCompleted`. "climate change misinformation" picked 101.7-141.6 s instead. With the 2nd new render forced to fail, both live clips stayed, the one new file was deleted, `RepromptFailed` was emitted and no `JobFailed`. A 10-40 s range gave one 30.00 s clip. Limits: in the default configuration (`segment_provider=chapter`, T011) only a time-range reprompt runs; the retired clips' files stay on disk until retention's `sweep_retired_files` deletes them after `retired_files_grace_hours` (T033); the in-flight guard is per process; PostgreSQL not exercised. |
| FR-017 | The system MUST list clips filtered by `job_id`, `min_score`, `search`, excluding retired clips. | Implemented (`min_score` is inclusive and counts an unscored clip as 0 on both stores; T024 fixed SQL, which dropped NULL scores) | `tests/contract/test_store_lookup_routes.py`, `test_media_router.py`, `tests/unit/test_job_store_lookups.py` |

**Editing and enhancement**

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-020 | The system MUST store, version, delete and plan-render a per-clip timeline. | Implemented (the plan only: no route renders a saved timeline into a video, and `TimelineRendered` is never emitted) | `tests/contract/test_clip_edits_router.py`, `tests/unit/test_clip_edit_model.py`, `test_timeline_render_service.py`; `GET …/edit/plan` returns `timeline_render_service.build_render_plan(...)` (`app/routers/clip_edits.py`) |
| FR-021 | The system MUST generate AI hook text for a clip through a local Ollama model (empty string on failure). | Implemented | `tests/contract/test_ai_hook_router.py` |
| FR-022 | The system MUST enhance clip audio with `loudnorm`, `rnnoise` or `passthrough` (202; 422 on unknown provider). | Implemented | `tests/contract/test_enhance_speech_router.py` |
| FR-023 | The system MUST apply animated captions, transitions, brand vocabulary, profanity filter and voice-over inside the pipeline. | **Scaffolded-unwired** | Services exist (`animated_caption_service`, `transition_service`, `brand_vocabulary_service`, `profanity_filter_service`, `voiceover_service`) with unit tests; none is imported by the orchestrator, and `timeline_render_service` (which builds a plan only) imports none of them. The UI labels the related settings (brand template page, auto transitions, caption templates) "Not applied to renders yet" (T013, UI half). A job's `brand_template_id` is stored and never read by the pipeline. Moved to roadmap spec 002-caption-brand-pipeline (`specs/README.md`). |

**Brand**

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-030 | The system MUST provide CRUD and asset upload for brand templates. | Implemented | `tests/contract/test_brand_templates_router.py` |

**Social publishing**

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-031 | The system MUST connect accounts (tokens Fernet-encrypted), publish immediately in the background, and list jobs by status. | Implemented | `tests/contract/test_social_publish_router.py`, `tests/unit/test_token_vault.py`, `test_social_adapters.py`. The publish page reads the clip it publishes with `GET /clips/{clip_id}` (T037): the item `GET /clips` returns for that clip, 404 `clip not found` for an unknown or retired id, at `/x` and `/api/x`, on both stores (`tests/contract/test_get_clip_by_id.py`). The id segment excludes dots (`_ClipIdConvertor`, `app/routers/clips.py`) so the route cannot take `GET /clips/bulk-export.zip` from `bulk_export`, which is included after `clips`. On the memory store (dev and tests; the default is `sql`) a clip holds only the keys the pipeline set, as in `GET /clips`. |
| FR-032 | ~~The system MUST publish at `schedule_at`.~~ | **Removed (owner decision 2026-10-09)** | T010: `schedule_at` dropped from the API and UI; `POST /social/publish` rejects it with 422 (`test_publish_create_rejects_schedule_at`). `publish_scheduler.py` and its test deleted. The `publish_jobs.schedule_at` column stays unused (constitution V, `docs/db-parity.md`). PR #41. T045 removed the leftovers of the W3 scheduler: the `/calendar` page, `app/services/scheduler_service.py`, the `ScheduledPostQueued` event and the `scheduler`/`bulk_schedule` capability flags. The `scheduled_posts` table stays unused (constitution V, `docs/db-parity.md`). |
| FR-033 | The system MUST support TikTok through a cookie session or an n8n sidecar. | Implemented | `tests/unit/test_tiktok_adapter.py`, `test_n8n_tiktok_adapter.py`, `test_registry_tiktok.py` |

**Export**

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-050 | The system MUST export a rendered clip as Premiere or DaVinci XML (409 when unrendered). | Implemented | `tests/contract/test_xml_export_router.py`, `tests/unit/test_xml_export_service.py` |
| FR-051 | The system MUST bulk-export non-retired clips as a zip with a manifest. | Implemented (was Partial at baseline; fixed by PR #29) | `app/routers/bulk_export.py` now selects only non-retired clips; ids that are all retired give 404, like an unknown id. Tests: `test_bulk_export_skips_retired_clips`, `test_bulk_export_only_retired_is_404` in `tests/contract/test_bulk_export.py`; dropping the filter turns both red. Manifest columns: `clip_id, title, summary, start, end, output_path, thumbnail_path, virality_score, hashtags, broll_credits`; `broll_credits` (T039) is the job manifest's JSON credit list, `[]` without B-roll, with no asset paths (`test_bulk_manifest_*`). |
| FR-052 | The system MUST write an export folder and `manifest.csv` for n8n hand-off. | Implemented | `tests/unit/test_export_service.py`, `test_manifest_service.py`; contract in `docs/social-publish-handoff.md` |

**Cross-cutting**

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-060 | The system MUST require an API key on every route when `YTVIDEO_REQUIRE_AUTH=true`. With auth on, FastAPI's docs surface (`/docs`, `/docs/oauth2-redirect`, `/redoc`, `/openapi.json` and their `/api/...` forms) MUST be switched off and answer the 404 of an unknown path; with auth off it is served. | Implemented (API routes; the docs surface is off with auth on: `create_app` passes `docs_url`, `redoc_url` and `openapi_url` as `None`, T043. The `serve_frontend` static mount is not an API route, so the app-level `dependencies` in `create_app` do not apply and it stays open) | `tests/contract/test_auth.py`: `/health`, `/clips`, `/jobs` and their `/api/...` forms return 401 with no key or a wrong key, 200 with `Authorization: Bearer <key>` or `?token=<key>`; open when auth is off. Docs: `test_auth_on_disables_the_docs_routes` (404 with or without the key, at both addresses), `test_auth_off_keeps_the_docs_routes` (200), `test_auth_on_keeps_the_schema_in_process` (`app.openapi()`, which the drift and prefix tests walk, still works). Under `serve_frontend` an HTML navigation to a switched-off docs path gets the 404, not the shell: `test_with_auth_the_switched_off_docs_get_the_404_not_the_shell` in `tests/contract/test_spa_fallback_routing.py`. The static mount bypass is pinned by `test_serve_frontend_with_auth_keeps_the_spa_open_and_the_api_closed` in `tests/contract/test_api_prefix_routing.py`. The same shell answers an HTML navigation to a client route without a key (T036), while API requests on the shared paths (`/jobs/abc` from `fetch`, `/api/jobs/abc`) still get 401: `test_with_auth_the_spa_shell_stays_open_and_the_api_closed` in `tests/contract/test_spa_fallback_routing.py`. Decision (T043, 2026-10-10, the owner may overrule): switch the docs off rather than keep them open, which contradicts "auth on means every route needs the key", or protect them, which cannot work in a browser: the Swagger page fetches `/openapi.json` itself and cannot send the key unless the token is embedded in its HTML. Switching auth off locally restores the docs for development. |
| FR-061 | The system MUST persist state in SQLite or PostgreSQL via Alembic migrations. | Implemented | `docs/db-parity.md`; 17 revisions in `alembic/versions/`. Every `clips` column the pipeline writes round-trips through both job stores (T029: the SQL store used to drop `ai_hook_text`, `ai_hook_audio_path`, `broll_assets`, `caption_style`, `captions_burnt_path` and `retired`); `tests/unit/test_job_store_clip_roundtrip.py` on SQLite. `JobStore.retire_clips(job_id, clip_ids)` flags a job's clips retired and returns how many it retired; it deletes no files (`tests/unit/test_job_store_retire_clips.py`). |
| FR-062 | The system MUST serve the React UI when `YTVIDEO_SERVE_FRONTEND=true`. | Implemented (was Partial: reloading a deep link got JSON; fixed by T036) | `/` and its assets are served and the UI's `/api/...` calls reach the API (T034): `test_serve_frontend_serves_the_spa_at_root`, `test_serve_frontend_health_is_json`, `test_serve_frontend_reaches_api_routes_under_api` in `tests/contract/test_api_prefix_routing.py`. Reloading a client route gets the UI: `SpaFallbackMiddleware` (`app/spa_fallback.py`, ADR-005 addendum) rewrites a request to `/index.html` when it is a GET or HEAD, its `Accept` prefers `text/html`, its path is not under `/api`, matches `CLIENT_ROUTES` (the 18 paths of `web/src/routeTree.ts`) and is not in `API_ONLY_PATHS` (`/clips/bulk-export.zip`, `/jobs/preview`), and `index.html` exists and the path is not a file in `web/dist`. Every other request gets the API unchanged: `/jobs/<id>` is the job page for a browser navigation and the job JSON for `fetch`, `curl` or `EventSource`, and both answers carry `Vary: Accept`. Tests: `tests/contract/test_spa_fallback_routing.py` (each client route; `Accept` variants; downloads, media, docs and `/api/...` not rewritten; static files win; non-GET; HEAD; `serve_frontend` off; no `index.html`; auth; SSE), `tests/unit/test_spa_fallback.py`, `tests/unit/test_spa_client_routes_drift.py` (parses `web/src` as text, no node; every fixed API GET path a client route would capture must be in `API_ONLY_PATHS`). 20 mutations (Accept, route table, method, `serve_frontend`, static-file check, `/api` strip, middleware order, `Vary`, both drift directions, ...) each turn tests red. Real run: uvicorn with the built `web/dist`; a headless Chrome reload of `/uploads/new`, `/settings/brand`, `/jobs/abc`, `/clips/xyz/edit` and `/workflow` rendered the React pages, and the page's own `fetch` of `/api/jobs/abc` still got 404 JSON. |

### Scaffolded or missing surfaces

Tables and services exist; no router exposes them (status `Missing` for the HTTP API, `Scaffolded-unwired` for the code): **analytics** (`ClipAnalyticsSnapshot`, `analytics_service`), **share links** (`ShareLink`, `share_link_service`), **webhooks** (`Webhook`, `webhook_dispatcher`), **workspaces and roles** (`Workspace`, `WorkspaceMember`; tables only, no service), **API tokens** (`ApiToken`, `api_token_service`). The `scheduled_posts` table (`ScheduledPost`) is kept but unused: scheduling was dropped (FR-032) and `scheduler_service` deleted (T045). UI routes `/team`, `/settings/api`, `/settings/webhooks`, `/share/$token` are static or placeholder pages with no backend calls; `/analytics` shows three counts computed from `GET /clips`, not analytics. `api_token_service` is reachable only through `app/auth.py` `current_user_id` when `YTVIDEO_AUTH_ENABLED=true` (one route, `POST /social/tiktok/connect`), and nothing issues a token. The sidebar no longer links to `/analytics` (hidden, route kept), and the webhooks and share-link pages say they are unavailable (T017, first half); none of the five is reachable from the navigation except by typing the URL. `/calendar` was removed (T045). `AUDIO_ENHANCED`, `FILLERS_REMOVED` and `AI_HOOK_GENERATED` are emitted by the orchestrator once per chapter after their stage succeeds (T016, `tests/unit/test_orchestrator_stage_events.py`), `BROLL_APPLIED` by its B-roll step (T012), and `PUBLISH_QUEUED`/`PUBLISH_COMPLETED`/`PUBLISH_FAILED` by `social_publish_service` (keyed by the publish job id). Never emitted: `XML_EXPORTED` and `TIMELINE_EDITED` (their services call `emit_from_sync`, but the routers pass no bus), `TIMELINE_RENDERED`, `VOICEOVER_GENERATED`, `ANIMATED_CAPTION_RENDERED`, `TRANSITIONS_APPLIED`, `BRAND_VOCAB_APPLIED`, `WEBHOOK_DISPATCHED`, `BULK_EXPORT_COMPLETED`, `SHARE_LINK_CREATED`, `ANALYTICS_REFRESHED`, and the older `SUBTITLE_IMAGE_RENDERED` and `UPLOAD_RECEIVED`. The five backends above are roadmap specs 003-007 (`specs/README.md`); scheduled posts were dropped with FR-032 (leftovers removed by T045).

### Key Entities

Defined in `app/db/models.py` (16 tables; fields are not copied here): `JobRecord`, `ChapterRecord`, `ClipRecord`, `BrandTemplate`, `BrandTemplateFont`, `ClipEdit`, `SocialAccount`, `PublishJob`, `CaptionStyle`, `Workspace`, `WorkspaceMember`, `ScheduledPost`, `ClipAnalyticsSnapshot`, `ShareLink`, `Webhook`, `ApiToken`. Domain events: `app/domain/events.py` (39 types; T045 removed `ScheduledPostQueued`). Pipeline options: `app/domain/models.py` `PipelineOptions`.

## Success Criteria *(mandatory)*

Only figures already measured in the repository are used.

- **SC-001**: A one-pass render of a 75.9 s 1920x1080 source with 3 chapters completes in about 18 s wall clock and about 1.3 GiB peak RSS (ADR-004, PR #17, single run on an M-series Mac).
- **SC-002**: Rendered output is yuv420p with even dimensions (ADR-004).
- **SC-003**: Caption words stay within one frame of the audio (`tests/sync_checker.py` accepts frame errors in (-1, +1)).
- **SC-004**: The default `pytest` run needs no network and passes on Python 3.14 (CI).
- **SC-005**: The frontend passes `pnpm test` and `pnpm build` (CI).

## Assumptions

- The baseline described `main` at `40ab44d`. The programme merged since (PRs #28-#54) is folded into the rows above and listed in *Changes since baseline*; new features get their own `specs/00N-<slug>/` (roadmap: `specs/README.md`).
- Status tags describe reachable behaviour in the running app, not the existence of code.
- Test files named as evidence exist; their strength was audited only where *Audit notes* says so.

## Audit notes

Strength of evidence was checked for four requirements on 2026-10-09 (mutation on a clean tree, reverted afterwards). Line numbers are those of the audited commit; the `min_score` filter is now `app/bus/job_store.py:200` (memory store).

| Req | Mutation | Result |
|---|---|---|
| FR-011 | `uploads.py`: skip the MIME check | `test_upload_wrong_mime_type_returns_415` **failed** (test is effective) |
| FR-022 | `enhance_speech.py`: `provider: Literal[…]` → `str` | `test_enhance_audio_unknown_provider_422` **failed** (effective) |
| FR-017 | `job_store.py:171`: `>= min_score` → `<= min_score` | **46 tests passed** (`test_store_lookup_routes.py`, `test_media_router.py`, `test_job_store_lookups.py`). The `min_score` filter had no effective test. **Resolved by T024**: the same mutation now fails `test_list_clips_min_score_is_inclusive_at_the_boundary[memory]` and `test_list_clips_min_score_filter_is_inclusive`; the SQL equivalent fails the `[sql]` cases. |
| FR-014 (before the fix) | none needed | Throwaway probe: `PATCH /clips/c1/like` returned `liked = True` while the store held `liked = None`, with `RuntimeWarning: coroutine 'like_clip.<locals>._toggle' was never awaited` at `job_store.py:106`. The existing like/dislike test passes against this broken code. |

All other `Implemented` tags are unaudited: they mean "reachable, with a named test file".

## Changes since baseline

Merged PRs since the baseline (`40ab44d`), all on 2026-10-09, in merge order. Status changes are for the row's tag at the baseline → now. PR #32 (a red-CI proof for the gitleaks job) was closed unmerged; #39 (Dependabot) is open.

| PR | Change | Requirement (status) | Tasks |
|---|---|---|---|
| #27 | Spec Kit adopted: constitution, this spec, tasks | | |
| #28 | Like/dislike persisted; one clip re-rendered in place from the saved source (`jobs.video_path`, migration `o3p4q5r6s7t8`) | FR-014 Partial → Implemented; FR-015 Partial → Implemented | T006, T007 |
| #29 | Bulk export skips retired clips | FR-051 Partial → Implemented | T009 |
| #30 | `CLAUDE.md` aligned with principle II; ADR-003 notes ADR-004 supersedes MoviePy; Pages build fixed | | T005, T023 |
| #31 | gitleaks in CI | Principle VI | T004 |
| #34 | Migrations aligned with models | FR-061 | T018 |
| #33 | Tests for `min_score` (and the SQL store's NULL scores), auth, unsupported platform; 3.12-only skips | FR-017; FR-060 Untested → Implemented; FR-001 | T019, T024, T026, T027 |
| #35 | Retention sweep extracted and tested; Linux caption goldens | FR-013 Untested → Implemented | T020, T025 |
| #36 | Env read through `Settings` | Principle I | T001, T002 |
| #37 | CI actions pinned to commit SHAs | | |
| #38 | Stage events emitted | FR-008 | T016 |
| #40 | Unused settings and the dead SSE heartbeat removed; sse-starlette keep-alive pings | FR-003 | T014, T015 |
| #41 | Scheduled publishing dropped | FR-032 Scaffolded-unwired → Removed | T010 |
| #42 | Per-job output folders; downloads and uploads out of `/tmp` | FR-002, FR-011, FR-013 | T030, T031 |
| #43 | numpy-only heuristic proposer | FR-009 | T011 (part 1) |
| #44 | Crop track in the one-pass render | FR-010 | T012 (render half of reframe) |
| #45 | The UI stops advertising missing features | FR-023, scaffolded surfaces | T013 (UI half), T017 (first half) |
| #46 | SQL store persists every clip field; `JobStore.retire_clips` | FR-008, FR-061 | T029 |
| #47 | yt-dlp merges with the bundled ffmpeg, previews run `python -m yt_dlp`; server-side default download path | Principle III; FR-001 | T003, T035 |
| #48 | Re-render follow-ups; `jobs.video_path` backfill script | FR-015 | T028 |
| #49 | Clip discovery in sources without chapters | FR-009 Scaffolded-unwired → Implemented (opt-in) | T011 |
| #50 | Every route at both `/x` and `/api/x` (ADR-005) | FR-062 Untested → Partial; FR-060 | T034 |
| #51 | Reprompt re-discovers clips from the retained source | FR-016 Partial → Implemented (opt-in) | T008 |
| #53 | B-roll overlays in the one-pass render | FR-010 | T012 |
| #52 | Face-tracked reframe (ADR-006) | FR-010 Scaffolded-unwired → Partial (reframe only) | T012 |
| #54 | B-roll planner and `local`/`pexels` providers | FR-010 Partial → Implemented (both opt-in) | T012 |
| #55 | Docs close-out: ADR-006 renumbered, ADR-007, roadmap stubs 002-007, rows re-verified | FR-009, FR-016 tags now say opt-in; FR-020 says plan only; FR-013 says SQL store only | T011, T013, T017 |
