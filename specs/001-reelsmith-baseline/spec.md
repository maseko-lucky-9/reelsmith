# Feature Specification: Reelsmith Baseline (inventory of current behaviour)

**Feature Branch**: `n/a (baseline inventory; not a feature branch)`
**Created**: 2026-10-09
**Status**: Accepted 2026-10-09 (owner sign-off)
**Input**: Retro-specification of the product as it exists at `main` `40ab44d`, derived from the code, tests, ADR-001…004 and the wave gate documents.

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
> Counts below were produced by commands, not recalled: 46 HTTP operations (`create_app().openapi()["paths"]`), 38 `EventType` members, 16 ORM tables, 17 Alembic revisions (16 at `main` `40ab44d`; the re-render fix added `o3p4q5r6s7t8`).

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
5. **Given** a source without chapters, **When** the job runs, **Then** a single "Full Video" chapter covers the whole probed duration.
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

**Independent Test**: `GET /clips?job_id=&min_score=&search=`, `PATCH /clips/{id}/like`, `POST /clips/{id}/rerender`, `POST /api/jobs/{id}/reprompt`.

**Acceptance Scenarios**:

1. **Given** clips exist, **When** listed with `job_id`, `min_score` or `search`, **Then** only matching, non-retired clips are returned.
2. **Given** a clip, **When** `PATCH /clips/{id}/like`, **Then** the response says liked **and the change persists on the next read** (FR-014, fixed).
3. **Given** a clip, **When** `POST /clips/{id}/rerender`, **Then** that clip is re-rendered in place from the job's saved source video and the job stays `completed`. A job with no saved source (created before `jobs.video_path`), or one not yet completed, gets 409 (FR-015, fixed).
4. **Given** a completed job, **When** `POST /api/jobs/{id}/reprompt` with a prompt and a length range, **Then** the segment proposer re-runs and new clips appear. *Does not today, see FR-016.*

### User Story 4 - Edit a clip on a timeline (Priority: P4)

A creator opens a clip in a multi-track editor, saves a timeline, and renders it. They can also add an AI hook line and enhance the audio.

**Why this priority**: differentiation from a plain clipper (ADR-003 Wave 1).

**Independent Test**: `PUT /api/clips/{id}/edit`, `GET …/edit/plan`, `POST …/ai-hook`, `POST …/enhance-audio`.

**Acceptance Scenarios**:

1. **Given** a clip, **When** a timeline is saved with `PUT`, **Then** `GET` returns it with an incremented version, and `DELETE` removes it.
2. **Given** a saved timeline, **When** `GET …/edit/plan`, **Then** a render plan is returned.
3. **Given** a clip with a transcript, **When** `POST …/ai-hook`, **Then** hook text is generated by the local Ollama model, truncated to `ai_hook_max_chars` (80); on any failure an empty string is returned, with no fallback text.
4. **Given** `provider` of `loudnorm`, `rnnoise` or `passthrough`, **When** `POST …/enhance-audio`, **Then** 202; an unknown provider gets 422 listing the allowed values.

### User Story 5 - Apply a brand (Priority: P5)

A creator manages brand templates (logo, font, colours, caption style, vocabulary) and attaches one to a job.

**Independent Test**: `POST /brand-templates`, `POST /brand-templates/{id}/assets`, then submit a job with `brand_template_id`.

**Acceptance Scenarios**:

1. **Given** valid input, **When** a template is created, read, updated and deleted, **Then** each call returns the expected status.
2. **Given** a logo or font file, **When** uploaded to a template, **Then** it is stored and referenced by the template.

### User Story 6 - Publish to social platforms (Priority: P6)

A creator connects accounts and publishes a clip now. Scheduled publishing is intended but not running.

**Independent Test**: `POST /social/accounts`, `POST /social/publish`, `GET /social/jobs?status=`.

**Acceptance Scenarios**:

1. **Given** a connected account and a clip, **When** `POST /social/publish` without `schedule_at`, **Then** a `queued` publish job runs in the background and its status is readable.
2. **Given** `schedule_at`, **When** `POST /social/publish`, **Then** a `pending` job is stored. *It is never picked up; the scheduler is not started, see FR-032.*
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
- A stage fails inside one chapter: the chapter task is bounded by `max_parallel_chapters`; the job status reflects failure through `JobFailed`.
- Auth: with `YTVIDEO_REQUIRE_AUTH=true`, every route requires the API key.

## Requirements *(mandatory)*

### Functional Requirements

**Ingest and pipeline**

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-001 | The system MUST accept a URL via `POST /jobs`, dedupe by URL, and reject unsupported platforms with 400. | Implemented (dedupe tested; the 400 for an unsupported URL has no test through `POST /jobs`) | `tests/contract/test_jobs_router.py`, `test_store_lookup_routes.py` |
| FR-002 | The system MUST support YouTube, TikTok, Instagram, Facebook, upload and generate sources through platform adapters. | Implemented | `tests/unit/test_platform_adapters.py`, `test_platform_registry.py` |
| FR-003 | The system MUST stream per-job events over SSE. | Implemented | `tests/contract/test_generate_pipeline.py`, `tests/e2e/test_happy_path.py` |
| FR-004 | The system MUST run at most `max_concurrent_jobs` jobs and `max_parallel_chapters` chapters at once. | Implemented | `tests/unit/test_orchestrator_concurrency.py` (4 tests fail on Python 3.12; 3.14 only) |
| FR-005 | The system MUST mark interrupted jobs failed at startup. | Implemented | `tests/unit/test_startup_recovery.py` |
| FR-006 | The system MUST transcribe with word-level timings (faster-whisper) or a stub. | Implemented | `tests/unit/test_transcription_service.py`, `test_transcription_whisper_path.py` |
| FR-007 | The system MUST render each chapter in one ffmpeg pass, to yuv420p with even dimensions, captions pixel-identical to the PIL renderer. | Implemented | `tests/unit/test_render_service.py`, `test_caption_track.py`, `tests/sync_checker.py` |
| FR-008 | The system MUST generate a thumbnail per rendered clip and, optionally, AI hook text, filler removal and audio enhancement. | Implemented | `tests/unit/test_thumbnail_service.py`, `test_ai_hook_service.py`, `test_filler_transition_profanity.py`, `test_audio_enhance_service.py` |
| FR-009 | The system MUST propose and score segments for sources without chapters. | **Scaffolded-unwired** | `segment_proposer` is gated at `app/workers/orchestrator.py:206`, but both branches build the same single "Full Video" chapter ("would run here (future)"). Service tested in isolation: `tests/unit/test_segment_proposer.py`. Clips are therefore **not** virality-ranked. |
| FR-010 | The system MUST honour the `reframe` and `broll` pipeline options (default on). | **Scaffolded-unwired** | `orchestrator.py:653-656` emits `StageSkipped` only inside the `render=False` branch; when they are on, nothing runs. Services exist: `reframe_service`, `active_speaker_service`, `broll_service`, `broll_pexels_service`. |
| FR-011 | The system MUST accept MP4 uploads (415 wrong type, 413 too large). | Implemented | `tests/contract/test_uploads_router.py` |
| FR-012 | The system MUST generate a video from a brief when `generate_enabled`, else 400. | Implemented | `tests/contract/test_generate_router.py`, `test_generate_pipeline.py`, `tests/unit/test_ltx_producer.py` |
| FR-013 | The system MUST retire clips older than `retention_days` (30) and delete their video and thumbnail files, on a sweep every `retention_sweep_minutes` (60). Jobs are not deleted. | Implemented | `app/services/retention.py` (`sweep_expired_clips`, looped by the lifespan janitor in `app/main.py`); `tests/unit/test_retention_sweep.py` (T025). The row is retired and committed before files are deleted; only `output_path` and `thumbnail_path` are deleted, never the job's source video. |

**Clip curation**

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-014 | The system MUST persist like and dislike toggles. | Implemented (fixed in this branch; was Partial) | Both stores now await async mutators (`app/bus/job_store.py` `_apply_clip_mutator`). Tests: `tests/unit/test_job_store_async_mutator.py` (memory and SQL), read-back tests in `tests/contract/test_store_lookup_routes.py`. Mutation-checked: removing the `await` turns 9 tests red. Before the fix a probe returned `liked=True` in the response and `None` in the store. |
| FR-015 | The system MUST re-render a single clip of a completed job in place, from its saved source video, without changing the job. | Implemented (fixed in this branch; was Partial) | `POST /clips/{id}/rerender` validates (404 unknown/retired clip; 409 job missing, not completed, or source video not retained), then `orchestrator._rerender_clip` re-runs `_process_chapter` for that clip id. `jobs.video_path` (migration `o3p4q5r6s7t8`) persists the source path. Tests: `tests/contract/test_clip_rerender_router.py`, `tests/unit/test_orchestrator_rerender.py`. A real run with the bundled ffmpeg replaced the clip file (3.0 s, H.264 yuv420p), kept `liked`, created no new clip, left no temp dir and left the job `completed`. Limits: jobs created before the migration return 409; `reframe_provider` is accepted but ignored (FR-010); a re-render regenerates AI hook, summary and hashtags; PostgreSQL not exercised. |
| FR-016 | The system MUST re-run segment proposal on reprompt. | **Partial** | `app/routers/reprompt.py:93` sets the job `pending` and rewrites options; nothing re-enqueues it, and the proposer is itself unwired (FR-009). The job then stays `pending`, which is in `_DEDUP_STATUSES` (`jobs.py:24`), so every later `POST /jobs` for that URL returns the stuck job. |
| FR-017 | The system MUST list clips filtered by `job_id`, `min_score`, `search`, excluding retired clips. | Implemented (the `min_score` filter is untested, see Audit notes) | `tests/contract/test_store_lookup_routes.py`, `test_media_router.py` |

**Editing and enhancement**

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-020 | The system MUST store, version, delete and plan-render a per-clip timeline. | Implemented | `tests/contract/test_clip_edits_router.py`, `tests/unit/test_clip_edit_model.py`, `test_timeline_render_service.py` |
| FR-021 | The system MUST generate AI hook text for a clip through a local Ollama model (empty string on failure). | Implemented | `tests/contract/test_ai_hook_router.py` |
| FR-022 | The system MUST enhance clip audio with `loudnorm`, `rnnoise` or `passthrough` (202; 422 on unknown provider). | Implemented | `tests/contract/test_enhance_speech_router.py` |
| FR-023 | The system MUST apply animated captions, transitions, brand vocabulary, profanity filter and voice-over inside the pipeline. | **Scaffolded-unwired** | Services exist (`animated_caption_service`, `transition_service`, `brand_vocabulary_service`, `profanity_filter_service`, `voiceover_service`) with unit tests; none is imported by the orchestrator, and `timeline_render_service` (which builds a plan only) imports none of them. |

**Brand**

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-030 | The system MUST provide CRUD and asset upload for brand templates. | Implemented | `tests/contract/test_brand_templates_router.py` |

**Social publishing**

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-031 | The system MUST connect accounts (tokens Fernet-encrypted), publish immediately in the background, and list jobs by status. | Implemented | `tests/contract/test_social_publish_router.py`, `tests/unit/test_token_vault.py`, `test_social_adapters.py` |
| FR-032 | The system MUST publish at `schedule_at`. | **Scaffolded-unwired** | `PublishScheduler` is referenced nowhere outside its own module; `app/main.py` never starts it. `tests/unit/test_publish_scheduler.py` tests it in isolation. |
| FR-033 | The system MUST support TikTok through a cookie session or an n8n sidecar. | Implemented | `tests/unit/test_tiktok_adapter.py`, `test_n8n_tiktok_adapter.py`, `test_registry_tiktok.py` |

**Export**

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-050 | The system MUST export a rendered clip as Premiere or DaVinci XML (409 when unrendered). | Implemented | `tests/contract/test_xml_export_router.py`, `tests/unit/test_xml_export_service.py` |
| FR-051 | The system MUST bulk-export non-retired clips as a zip with a manifest. | Implemented (fixed in this branch; was Partial) | `app/routers/bulk_export.py` now selects only non-retired clips; ids that are all retired give 404, like an unknown id. Tests: `test_bulk_export_skips_retired_clips`, `test_bulk_export_only_retired_is_404` in `tests/contract/test_bulk_export.py`; dropping the filter turns both red. |
| FR-052 | The system MUST write an export folder and `manifest.csv` for n8n hand-off. | Implemented | `tests/unit/test_export_service.py`, `test_manifest_service.py`; contract in `docs/social-publish-handoff.md` |

**Cross-cutting**

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-060 | The system MUST require an API key on every route when `YTVIDEO_REQUIRE_AUTH=true`. | Untested | Probe: every route returns 401 without the key, including `/health`. No test references `require_auth` or `require_api_key` (T026). |
| FR-061 | The system MUST persist state in SQLite or PostgreSQL via Alembic migrations. | Implemented | `docs/db-parity.md`; 16 revisions in `alembic/versions/` |
| FR-062 | The system MUST serve the React UI when `YTVIDEO_SERVE_FRONTEND=true`. | Untested | `app/main.py`; no backend test covers `serve_frontend` (the Vitest suite tests components only) |

### Scaffolded or missing surfaces

Tables and services exist; no router exposes them (status `Missing` for the HTTP API, `Scaffolded-unwired` for the code): **analytics** (`ClipAnalyticsSnapshot`, `analytics_service`), **share links** (`ShareLink`, `share_link_service`), **webhooks** (`Webhook`, `webhook_dispatcher`), **workspaces and roles** (`Workspace`, `WorkspaceMember`), **API tokens** (`ApiToken`, `api_token_service`), **scheduled posts** (`ScheduledPost`, `scheduler_service`). UI routes `/analytics`, `/calendar`, `/team`, `/settings/api`, `/settings/webhooks`, `/share/$token` are static or placeholder pages with no backend calls. `AUDIO_ENHANCED`, `FILLERS_REMOVED` and `AI_HOOK_GENERATED` exist in `EventType` but the orchestrator marks them "TODO: emit".

### Key Entities

Defined in `app/db/models.py` (16 tables; fields are not copied here): `JobRecord`, `ChapterRecord`, `ClipRecord`, `BrandTemplate`, `BrandTemplateFont`, `ClipEdit`, `SocialAccount`, `PublishJob`, `CaptionStyle`, `Workspace`, `WorkspaceMember`, `ScheduledPost`, `ClipAnalyticsSnapshot`, `ShareLink`, `Webhook`, `ApiToken`. Domain events: `app/domain/events.py` (38 types). Pipeline options: `app/domain/models.py` `PipelineOptions`.

## Success Criteria *(mandatory)*

Only figures already measured in the repository are used.

- **SC-001**: A one-pass render of a 75.9 s 1920x1080 source with 3 chapters completes in about 18 s wall clock and about 1.3 GiB peak RSS (ADR-004, PR #17, single run on an M-series Mac).
- **SC-002**: Rendered output is yuv420p with even dimensions (ADR-004).
- **SC-003**: Caption words stay within one frame of the audio (`tests/sync_checker.py` accepts frame errors in (-1, +1)).
- **SC-004**: The default `pytest` run needs no network and passes on Python 3.14 (CI).
- **SC-005**: The frontend passes `pnpm test` and `pnpm build` (CI).

## Assumptions

- The baseline describes `main` at `40ab44d`; later changes are specified in `specs/002-…`.
- Status tags describe reachable behaviour in the running app, not the existence of code.
- Test files named as evidence exist; their strength was audited only where *Audit notes* says so.

## Audit notes

Strength of evidence was checked for four requirements on 2026-10-09 (mutation on a clean tree, reverted afterwards):

| Req | Mutation | Result |
|---|---|---|
| FR-011 | `uploads.py`: skip the MIME check | `test_upload_wrong_mime_type_returns_415` **failed** (test is effective) |
| FR-022 | `enhance_speech.py`: `provider: Literal[…]` → `str` | `test_enhance_audio_unknown_provider_422` **failed** (effective) |
| FR-017 | `job_store.py:171`: `>= min_score` → `<= min_score` | **46 tests passed** (`test_store_lookup_routes.py`, `test_media_router.py`, `test_job_store_lookups.py`). The `min_score` filter has no effective test (task T024). |
| FR-014 (before the fix) | none needed | Throwaway probe: `PATCH /clips/c1/like` returned `liked = True` while the store held `liked = None`, with `RuntimeWarning: coroutine 'like_clip.<locals>._toggle' was never awaited` at `job_store.py:106`. The existing like/dislike test passes against this broken code. |

All other `Implemented` tags are unaudited: they mean "reachable, with a named test file".
