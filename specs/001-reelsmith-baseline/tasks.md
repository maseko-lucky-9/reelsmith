# Tasks: Reelsmith Baseline

**Input**: `specs/001-reelsmith-baseline/spec.md` and `.specify/memory/constitution.md`
**Scope**: open defects and constitution exceptions found while writing the baseline. Nothing here is started.
**History**: completed work (Waves 0 to 3, T-01…T-07 of the parity programme) is in `tasks/todo.md`.

Format: `- [ ] T### [FR-xxx|E#] [Principle] description. Proving test → commit message`. Items marked **Decision** need an owner choice before work starts (wire it, or delete it).

## Phase 1: Constitution exceptions

- [x] T001 [E1] [I] (done: `settings.skip_alembic`, no fallback; `tests/unit/test_skip_alembic.py` proves the old name no longer skips) Rename `SKIP_ALEMBIC` to `YTVIDEO_SKIP_ALEMBIC` in `app/main.py:73` and `tests/unit/test_startup_recovery.py:47`. Test: app starts with the old name unset and migrations skipped under the new one → `fix(config): prefix SKIP_ALEMBIC with YTVIDEO_`
- [x] T002 [E2] [I] (done: plus `social_provider_<platform>` for the five platforms; `oauth_encrypt_key` was already declared, `token_vault` now reads only `settings`; `tests/unit/test_settings_env_reads.py`. Behaviour change: social provider selection now honours `.env`) Declare `log_level`, `piper_model`, `share_link_secret`, `social_provider` in `Settings` and read them from `settings` in `logging_config.py:18`, `voiceover_service.py:137`, `token_vault.py:29`, `share_link_service.py:51`, `social/registry.py:22,25`. Test: existing unit tests plus a settings test per field → `refactor(config): read env through Settings`
- [ ] T003 [E3,E4] [III] Pass the bundled ffmpeg to yt-dlp (`ffmpeg_location`) in `app/services/platforms/_yt_dlp_base.py`, and use the yt-dlp Python API instead of the `yt-dlp` CLI in `app/routers/jobs.py:59,87`. Test: unit test asserting `ffmpeg_location` equals the imageio-ffmpeg path → `fix(download): use bundled ffmpeg for yt-dlp merges`
- [x] T004 [E5] [VI] Run gitleaks in CI (`ci.yml`) and document `pre-commit install` in README. Test: CI job fails on a seeded fake key in a throwaway branch → `ci: scan for secrets with gitleaks`
- [x] T005 [E6] [II] Correct the "all inter-service communication goes through the event bus" line in `CLAUDE.md` to match constitution principle II. Test: n/a (docs) → `docs: align CLAUDE.md with constitution principle II`

## Phase 2: Defects in shipped behaviour

- [x] T006 [FR-014] [II] (done) Make `JobStore.upsert_clip` await async mutators in both stores (`app/bus/job_store.py:106` memory; SQL store `upsert_clip` at `:289`, call at `:314`, the default store), and replace the weak assertions at `tests/contract/test_store_lookup_routes.py:199` with a read-back of `liked`/`disliked`. Test must fail with the `await` removed → `fix(clips): persist like and dislike`
- [x] T007 [FR-015] [II] (done, owner chose the real re-render) The orchestrator now handles `rerender_clip_id`; `jobs.video_path` persists the source; the UI shows the 409 reason. → `fix(clips): re-render a single clip in place`
- [ ] T028 [FR-015] [II] Follow-ups to T007: (a) a failed re-render can leave the chapter status at `rendering` in the memory store; (b) jobs created before migration `o3p4q5r6s7t8` can never be re-rendered (no saved source); consider a backfill by matching clips' folders; (c) the source video is now relied on, so retention (`retention_days`) must not delete it (covered by T025: `test_job_and_source_video_are_untouched`); (d) `ai_hook_text`, summary and hashtags regenerate on re-render, add an option to skip. Test: failed-render leaves chapter `completed` → `fix(clips): reset chapter status after a failed re-render`
- [ ] T008 [FR-016] [II] **Decision** (depends on T011; today the job is left `pending` and blocks new submissions of its URL): re-enqueue the job after `POST /api/jobs/{id}/reprompt` and emit an event. Test: contract test asserts the job is queued → `fix(reprompt): enqueue the job`
- [x] T009 [FR-051] [II] (done) Exclude retired clips from the bulk-export manifest in `app/routers/bulk_export.py`. Test: retired clip absent from manifest (fails before the fix) → `fix(export): skip retired clips in bulk manifest`

## Phase 3: Scaffolded-unwired code (decide: wire or delete)

- [ ] T010 [FR-032] **Decision**: start `PublishScheduler` in the lifespan, or drop `schedule_at` from the API and UI. Test: a job with a past `schedule_at` gets published → `feat(social): run the publish scheduler`
- [ ] T011 [FR-009] **Decision**: call `segment_proposer` at `app/workers/orchestrator.py:206` when a source has no chapters, or remove the option. Fix the overclaims in `docs/architecture.md:83` and `README.md:5` either way. Test: job on a chapterless fixture yields more than one scored clip → `feat(pipeline): run the segment proposer`
- [ ] T012 [FR-010] (reframe render support done; wiring in 6.2: `render_service.render_clip(crop_track=...)` pans a canvas-aspect crop through `crop_x_expr`, nothing passes a track yet; B-roll untouched) **Decision**: wire reframe and B-roll, or default `reframe` and `broll` to off in `PipelineOptions`. Test: options gating test → `fix(pipeline): stop defaulting unwired stages on`
- [ ] T013 [FR-023] **Decision**: wire animated captions, transitions, brand vocabulary, profanity filter and voice-over into the orchestrator, or mark them timeline-only. Spec 002 candidate.
- [x] T014 (done: deleted all but `pexels_api_key` and `broll_cache_dir`, kept as a reasoned allowlist for the B-roll wiring; `tests/unit/test_settings_module.py` asserts the deleted names stay gone and every other field is read in `app/`) Remove or use these settings that no code reads: `scheduler_enabled`, `scheduler_poll_seconds`, `scheduler_max_concurrent`, `tiktok_profile_url_base`, `tiktok_node_bin`, `pexels_api_key`, `broll_cache_dir`, `ltx_model_path`, `ltx_use_mps`, `ltx_num_frames`, `stage_timeout_seconds`. Test: `tests/unit/test_settings_module.py` → `chore(config): drop unused settings`
- [x] T015 (done: deleted; the job SSE stream now passes `ping=settings.sse_keepalive_seconds` to sse-starlette's `EventSourceResponse`; `tests/unit/test_sse_keepalive.py`) Use or delete `app/sse_heartbeat.py` (referenced by no route). Test: SSE keepalive test → `chore: remove unused sse_heartbeat`
- [x] T016 (done: emitted by the orchestrator right after each stage succeeds, never when the stage is off, skipped or failed; payloads carry `chapter_index`, plus `clip_id` for the hook; `tests/unit/test_orchestrator_stage_events.py`) Emit `AUDIO_ENHANCED`, `FILLERS_REMOVED`, `AI_HOOK_GENERATED` from the orchestrator (marked TODO), or delete the enum members. Test: orchestrator event-order test → `feat(pipeline): emit stage events`
- [ ] T017 Missing backends for UI pages `/analytics`, `/calendar`, `/team`, `/settings/api`, `/settings/webhooks`, `/share/$token`. Each needs its own spec (`specs/002-…`) before work.

## Phase 4: Tooling and documentation

- [x] T018 (done, models aligned to the migrations; no new revision) `alembic check` reports 13 drift operations between migrations and models (11 index add/remove, 2 unique-constraint removals on `clip_edits` and `social_accounts`); `alembic upgrade --sql` fails on a data migration. Test: `alembic check` exits 0 (CI, Postgres) and `tests/unit/test_alembic_parity.py` (SQLite + offline SQL) → `fix(db): align migrations with models`
- [x] T019 (done: `requires_py313` skipif on the 4 chapter-failure tests; 3.12's TaskGroup leaves `cancelling()==1` after a child fails) Skip or fix the 4 tests in `tests/unit/test_orchestrator_concurrency.py` that fail on Python 3.12 (CI and docs are 3.14 only). → `test: mark concurrency tests 3.14-only`
- [x] T020 (done: `(True, "Linux", "x86_64")` seeded from the CI runner; the basic-layout Linux key is not seeded because CI has raqm) Seed Linux golden caption hashes in `tests/unit/test_subtitle_image_golden.py` (CI skips them today; the PNG-equality tests are the guard). → `test: seed linux caption goldens`
- [ ] T021 Set ruff `target-version` (reports F821 on `ExceptionGroup`). → `chore(lint): set ruff target-version`
- [x] T022 (closed, blocked upstream 2026-10-09) typescript-eslint 8.71.1 has peer `typescript <6.1.0` and TypeScript latest is 7.0.2. Revisit when typescript-eslint publishes a release that supports 7.1+.
- [x] T023 Add a "superseded by ADR-004" note to ADR-003 decision 2 (it names MoviePy). `docs/wave-3-gate.md` is a dated gate record: do not edit it (it says 12 revisions at `:37` and `:94`; `46c8200` actually contained 14 migration files; there are 16 now).

- [x] T024 [FR-017] [IV] (done: boundary tests on both stores and `GET /clips?min_score=50`; also fixed SQL dropping NULL scores, which the memory store counts as 0) Add a test for the `min_score` filter on `GET /clips`; mutating `>=` to `<=` at `app/bus/job_store.py:171` currently leaves 46 tests green. Test: fails under that mutation → `test(clips): cover min_score filter`

- [x] T025 [FR-013] [IV] (done: sweep extracted to `app/services/retention.py`, tests in `tests/unit/test_retention_sweep.py`; also retires before deleting and logs unlink failures) Add a test for the retention janitor (clips older than `retention_days` are retired and their files removed); extract the sweep body from `app/main.py:132-165` into a function if needed. Test: fails when the `created_at < cutoff` filter is inverted → `test(retention): cover the clip janitor`
- [x] T026 [FR-060] [IV] (done: `tests/contract/test_auth.py`; `/docs`, `/redoc`, `/openapi.json` and the static mount bypass the app-level dependency, see FR-060) Add a test that every route, including `/health`, returns 401 without the key when `YTVIDEO_REQUIRE_AUTH=true`, and 200 with it. Test: fails when `dependencies` is dropped in `create_app` → `test(auth): cover require_api_key`
- [x] T027 [FR-001] [IV] (done) Add a contract test that `POST /jobs` with an unsupported URL returns 400 with the URL echoed in `detail` (`{"detail": "Unsupported platform for URL: <url>"}`; there is no `url` key, that shape belongs to the `UnsupportedPlatformError` handler in `create_app`, which `POST /jobs` does not reach). → `test(jobs): cover unsupported platform`

## Dependencies

- T008 depends on T011 (reprompt needs a working proposer).
- T017 and T013 become `specs/002-…` rather than tasks here.
- Phase 1 has no dependencies and can run in parallel; T006 and T009 touch different files.
