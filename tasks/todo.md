# ReelSmith → OpusClip Parity — Task Tracker

**Plan:** [ADR-003](../docs/decisions/003-opusclip-feature-parity.md) · [URP](../docs/research/opusclip-parity-urp.md)
**Loop config:** [tasks/loop-config.yaml](loop-config.yaml)
**Started:** 2026-05-10

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
