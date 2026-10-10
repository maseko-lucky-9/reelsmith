# ReelSmith → OpusClip Parity — Task Tracker

**Plan:** [ADR-003](../docs/decisions/003-opusclip-feature-parity.md) · [URP](../docs/research/opusclip-parity-urp.md)
**Loop config:** [tasks/loop-config.yaml](loop-config.yaml)
**Started:** 2026-05-10

## Spec Kit index (open work lives here)

Open work is tracked per feature in `specs/*/tasks.md`; this file keeps the finished parity programme below.

| Spec | Tasks | State |
|---|---|---|
| [001 Reelsmith baseline](../specs/001-reelsmith-baseline/spec.md) | [tasks.md](../specs/001-reelsmith-baseline/tasks.md) | 9 open: T021, T032 (lint, wait on a `pyproject.toml` OK), backlog T038-T044 |
| [002-007 roadmap stubs](../specs/README.md) | none yet | stubs only: caption/brand pipeline, analytics, share links, webhooks, API tokens, workspaces |

Constitution: [.specify/memory/constitution.md](../.specify/memory/constitution.md)

### Spec Kit programme (baseline tasks) — ✅ SHIPPED 2026-10-09 (PRs #27-#54)

All merged on 2026-10-09, in merge order. PR #32 (gitleaks red proof) was closed unmerged.

- #27 docs: Spec Kit adopted (constitution, baseline spec, tasks)
- #28 like/dislike persisted; single-clip re-render in place (T006, T007)
- #29 bulk export skips retired clips (T009)
- #30 CLAUDE.md aligned with principle II; ADR-003 superseded note; Pages build (T005, T023)
- #31 gitleaks in CI (T004)
- #34 migrations aligned with models (T018)
- #33 tests: min_score, auth, unsupported platform; 3.12-only skips (T019, T024, T026, T027)
- #35 retention janitor tested; Linux caption goldens (T020, T025)
- #36 env read through Settings (T001, T002)
- #37 CI actions pinned to SHAs
- #38 stage events emitted (T016)
- #40 unused settings and dead SSE heartbeat removed (T014, T015)
- #41 scheduled publishing dropped (T010)
- #42 per-job output folders; downloads out of `/tmp` (T030, T031)
- #43 numpy-only heuristic proposer (T011 part 1)
- #44 crop track in the one-pass render (T012)
- #45 UI stops advertising missing features (T013, T017 UI halves)
- #46 SQL store keeps every clip field; `retire_clips` (T029)
- #47 yt-dlp uses the bundled ffmpeg; server-side download path default (T003, T035)
- #48 re-render follow-ups; `video_path` backfill (T028)
- #49 clip discovery in chapterless sources (T011)
- #50 every route at `/x` and `/api/x` (T034, ADR-005)
- #51 reprompt from the retained source (T008)
- #53 B-roll overlays in the render (T012)
- #52 face-tracked reframe (T012, ADR-006)
- #54 B-roll planner and providers (T012)
- #55 docs close-out (this review): ADR-006/007, roadmap stubs, spec re-verified, backlog T036-T045 (T011, T013, T017)

### Parity programme T-01…T-07 — ✅ SHIPPED (`29e0b6d`, PR #4)

- [x] **T-01** thumbnail-text-composite (`compose_thumbnail`, `app/services/thumbnail_service.py`)
- [x] **T-02** brand-vocabulary-tests (`tests/unit/test_brand_vocabulary_service.py`)
- [x] **T-03** voiceover-piper-provider (`app/services/voiceover_service.py`)
- [x] **T-04** xml-export-davinci-multitrack-stub (`app/services/xml_export_service.py`)
- [x] **T-05** social-scheduler-list-endpoint (`GET /social/jobs`)
- [x] **T-06** animated-caption-styled-burn (`burn_animated_captions`)
- [x] **T-07** audio-enhance-router (`POST /api/clips/{id}/enhance-audio`)

The original acceptance criteria are preserved in `docs/archive/TASKS.md`.

---

## Pre-flight (Wave 0) — ✅ MERGED to local main 2026-05-10

- [x] **PR-INTRO** — ADR-003 + URP report + tasks/todo.md (merge `f886c8f`, source `7a71002`)
- [x] **PR-0a** — Remove `streamlit==1.56.0` from `requirements.txt` (merge `673b347`, source `fe7f331`)
- [x] **PR-0b** — Snapshot test for `app/services/broll_service.py::find_broll` (merge `e4d6693`, source `dba3606`) — 17/17 green
- [x] **PR-0c** — gitleaks pre-commit + `.gitignore` audit (merge `b8824d5`, source `7def175`)
- [x] **PR-0d** — SQLite vs Postgres parity audit (merge `b9c874a`, source `c48ca98`)
- [x] **PR-0e** — Loop-monitor config (`tasks/loop-config.yaml`) (merge `9348204`, source `5b169de`)

## Wave 1 — Stub replacement + inline editor — ✅ MERGED 2026-05-10

### Backend (PRs)
- [x] W1.1 — `clip_edits` migration + ORM model
- [x] W1.2 — `clip_edits` CRUD router + render-plan endpoint
- [x] W1.3 — `social_accounts` migration + Fernet token vault
- [x] W1.4 — `publish_jobs` migration + APScheduler scaffold
- [x] W1.5 — platform adapters (stub default + YouTube live) + orchestrator
- [x] W1.6 — `social_publish` + `xml_export` routers + Jinja2 templates
- [x] W1.7 — `clip_ai_hook` migration + `ai_hook_service.py` + router
- [x] W1.8 — `audio_enhance_service.py` (loudnorm / rnnoise / passthrough) + router
- [x] W1.9 — `broll_assets` migration + `broll_pexels_service.py` + LRU cache
- [x] W1.10 — Reprompt endpoint + custom clip length range in `PipelineOptions`

### Frontend (PRs)
- [x] W1.11 — Replace `<ComingSoonButton>` with real menus on `ClipListRow.tsx`
- [x] W1.12 — `MultiTrackTimeline.tsx` + `useTimelineEditor` + `timeline_render_service.py`
- [x] W1.13 — Wire Undo/Redo/Save on `clips.$clipId.edit.tsx`
- [x] W1.14 — `settings.social.tsx` + `clips.$clipId.publish.tsx`

### Wave gate
- [x] W1.15 — `scripts/deploy.sh` (volume-safe, tar-snapshot, --no-recreate); `docs/wave-1-gate.md` summary; per-PR ladder green (319/319 pytest, 111/111 vitest, build green). Per-wave Docker deploy is operator-driven.

## Wave 2 — AI quality + reframe — ✅ MERGED 2026-05-10

### Backend
- [x] W2.1 — `animated_caption_service.py` (6 presets) + migration
- [x] W2.2 — `active_speaker_service.py` (smooth_cues + split-screen heuristic)
- [x] W2.3 — `voiceover_service.py` (Coqui / Piper / stub) + WAV header
- [x] W2.4 — `audio_enhance_service.py` `demucs` provider (opt-in)
- [x] W2.5 — `filler_removal_service.py` (lexicon + silence-gap coalescing)
- [x] W2.6 — `transition_service.py` (fade / slide / zoom xfade argv)
- [x] W2.7 — `brand_vocabulary_service.py` + migration
- [x] W2.8 — `brand_template_fonts` table (multi-font per template)
- [x] W2.9 — `profanity_filter_service.py`
- [x] W2.10 — SSE `with_heartbeat` + Postgres pool_recycle + stage_timeout

### Frontend
- [x] W2.11 — `CaptionTemplatePicker` + `/settings/captions`
- [x] W2.12 — `ReframeLayoutPicker`
- [x] W2.13 — `TransitionPicker` + `VocabularyEditor`
- [x] W2.14 — Editor RIGHT_TOOLS panels (component shape; integration follow-up)

### Wave gate
- [x] W2.15 — `docs/wave-2-gate.md`; per-PR ladder green (370/370 pytest, 119/119 vitest, build green). Per-wave Docker deploy + voiceover compose profile is operator-driven via `scripts/deploy.sh`.

## Wave 3 — Collab / integrations — ✅ MERGED 2026-05-10 (W3.10 deferred)

### Backend
- [x] W3.1 — Migration bundle: workspaces / members / scheduled_posts / analytics / share_links / webhooks / api_tokens
- [x] W3.2 — `scheduler_service.claim_due_posts` + `mark_published` (Postgres `SKIP LOCKED`)
- [x] W3.3 — `analytics_service` (record / latest_per_platform / aggregate)
- [x] W3.4 — `share_link_service` (HMAC `rs.<payload>.<sig>` tokens)
- [x] W3.5 — `webhook_dispatcher` (HMAC-SHA256 + 5xx retry budget)
- [x] W3.6 — `api_token_service` (bcrypt + constant-time match)
- [x] W3.7 — `bulk_export` router (`/api/clips/bulk-export.zip`)
- [x] W3.8 — `auth.current_user_id` + `current_workspace_id`
- [x] W3.9 — `capabilities.py` flag map (BUSINESS default)
- [ ] W3.10 — **DEFERRED** — pyannote diarisation + speaker-coloured captions (per ADR-003 §A.15; non-blocking for parity gate)

### Frontend
- [x] W3.11 — `/team`, `/calendar`, `/analytics`
- [x] W3.12 — `/settings/api`, `/settings/webhooks`
- [x] W3.13 — `/share/$token`
- [x] W3.14 — `useAutoSave` covered by W1.13 `useTimelineEditor`
- [x] W3.15 — Sidebar `placeholder: true` cleared; "Download 4K" stub already removed in W1.11

### Wave gate
- [x] W3.16 — `docs/wave-3-gate.md`; per-PR ladder green (408/408 pytest, 119/119 vitest, build green). Per-wave deploy + Postgres scheduler worker is operator-driven via `scripts/deploy.sh`.

---

## Review section

| Wave | PRs | Backend tests | Frontend tests | Notes |
|---|---|---|---|---|
| Pre-flight | 6 | covered by relevant suites | n/a | streamlit dropped, gitleaks active, broll snapshot locked |
| Wave 1 | 15 | suite reaches 319/319 | 111/111 | inline editor + publish + XML export + AI hook + enhance + Pexels broll + reprompt |
| Wave 2 | 12 | suite reaches 370/370 | 119/119 | animated captions, voice-over, demucs, filler/transitions/profanity, brand vocab + multi-font, SSE heartbeat |
| Wave 3 | 10 | suite reaches 408/408 | 119/119 | workspaces + scheduler (Postgres SKIP LOCKED) + analytics + share links + webhooks + api tokens + bulk export + auth/capabilities; W3.10 deferred |
| Total | **52** | **408 / 408** | **119 / 119** | local main 64 commits ahead of origin/main; nothing pushed |

---

## Review: Spec Kit programme close-out (2026-10-09)

**Done**

- **Baseline tasks.** 32 of T001-T035 are ticked. T013 and T017 are ticked as MOVED to roadmap specs 002-007, not built. T021, T032 and T033 stay open.
- **ADRs.** The two ADRs numbered 005 were split: the face-track ADR is now 006. Clip discovery and reprompt got ADR-007. ADR-001…007 are indexed in the README and the baseline plan.
- **README and `docs/architecture.md`.** They now separate the default pipeline, the opt-in stages (discovery and reprompt, face-track reframe, B-roll), what was removed (scheduled publishing) and the code no job uses. `YTVIDEO_DEFAULT_DOWNLOAD_PATH` is documented.
- **Spec 001.** Every row was re-checked against `67dd68d`. Status wording changed for FR-009, FR-016 (opt-in), FR-020 (plan only) and FR-013 (SQL store only), and the counts were re-run. A *Changes since baseline* table was added.
- **Roadmap and backlog.** The stubs live in `specs/README.md`. The backlog is T036-T045 in `specs/001-reelsmith-baseline/tasks.md`.

**Verified how**

- **Claims.** Each changed claim was checked by reading or grepping the code at `67dd68d`. File and line citations are in spec 001 and ADR-007.
- **Counts, by command.** `create_app().openapi()["paths"]` gives 46 operations on 38 paths. `len(EventType)` is 40. `Base.metadata.tables` has 16 tables. `ls alembic/versions` lists 17 revisions.
- **Probes.**
  - `serve_frontend` deep links return JSON 404s (T036).
  - `GET /api/clips/{id}` returns 404 (T037).
  - A map of which `EventType` members `app/` emits backs the never-emitted list.
  - `ruff check --target-version py314 .` gives 91 findings (T032).
- **`pytest -q`.** 1968 passed, 21 deselected on `main` and on the branch (docs only).
- **Links.** The markdown link check (`p8_links.py`, anchors included) found 79 relative links, 0 broken. It was mutation-checked on a probe tree with a broken file and a broken anchor.
- **Secrets.** `gitleaks detect --log-opts origin/main..HEAD` found no leaks.
- **Independent check.** A separate verifier pass re-checked about 170 of the added claims against the code. It found 2 false (in the roadmap stubs: TikTok's live adapters, the workspace roles), an ADR-003 "§A.15" reference that does not exist, and 3 imprecisions (one line number, the selection-rule order, the APScheduler mentions). All six were fixed before merge.

**Left**

- **Open tasks.** 13: T021 and T032 (wait on an OK for the `pyproject.toml` change), T033 (disk growth), and the backlog T036-T045.
- **Owner decisions.** T040 (ranking and the `segment_provider` default, gate G1), T043 (docs routes and auth), T044 (`requirements.txt` edit). Gate G2 (face track) is not signed off.
- **Roadmap.** Specs 002-007 are stubs; none has a `specs/00N-*/` directory yet.
