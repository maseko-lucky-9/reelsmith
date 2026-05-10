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

## Wave 1 — Stub replacement + inline editor

### Backend (PRs)
- [ ] W1.1 — Migration `c1d2e3f4g5h6_add_clip_edits` + ORM model
- [ ] W1.2 — Router `clip_edits.py` (CRUD timeline) + `timeline_render_service.py`
- [ ] W1.3 — Migration `d2e3f4g5h6i7_add_social_accounts` + Fernet token encryption
- [ ] W1.4 — Migration `e3f4g5h6i7j8_add_publish_jobs` + APScheduler scaffold
- [ ] W1.5 — `social_publish_service.py` + 5 platform adapters (YouTube real; TikTok/IG/LinkedIn/X stub)
- [ ] W1.6 — Router `social_publish.py` + `xml_export.py` + Jinja2 templates
- [ ] W1.7 — Migration `f4g5h6i7j8k9_add_clip_ai_hook` + `ai_hook_service.py` + router
- [ ] W1.8 — `audio_enhance_service.py` (loudnorm/RNNoise/passthrough) + router
- [ ] W1.9 — Migration `g5h6i7j8k9l0_add_broll_attribution` + `broll_pexels_service.py` + LRU cache
- [ ] W1.10 — Reprompt endpoint + custom clip length range in `PipelineOptions`

### Frontend (PRs)
- [ ] W1.11 — Replace `<ComingSoonButton>` with real menus on `ClipListRow.tsx`
- [ ] W1.12 — `MultiTrackTimeline.tsx` + `TextOverlayInspector.tsx` + `useTimelineEditor` hook
- [ ] W1.13 — Wire Undo/Redo/Save on `clips.$clipId.edit.tsx`
- [ ] W1.14 — `settings.social.tsx` + `clips.$clipId.publish.tsx`

### Wave gate
- [ ] W1.15 — Full ladder + tar-snapshot + `docker compose up -d --no-recreate` + `alembic upgrade head` + SSE smoke

## Wave 2 — AI quality + reframe

### Backend
- [ ] W2.1 — `animated_caption_service.py` (5 templates: Hormozi/MrBeast/Karaoke/BoldPop/Subtle) + migration
- [ ] W2.2 — `active_speaker_service.py` + reframe layout picker
- [ ] W2.3 — `voiceover_service.py` (Coqui XTTS v2) + migration + compose profile
- [ ] W2.4 — `audio_enhance_service.py` `demucs` provider (opt-in)
- [ ] W2.5 — `filler_removal_service.py` (webrtcvad + spaCy + lexicon)
- [ ] W2.6 — `transition_service.py`
- [ ] W2.7 — `brand_vocabulary_service.py` + migration
- [ ] W2.8 — Custom font upload (multi-font per brand template) + migration
- [ ] W2.9 — `profanity_filter_service.py`
- [ ] W2.10 — Long-stage SSE heartbeat + pool_pre_ping + stage timeout

### Frontend
- [ ] W2.11 — `settings.captions.tsx` (template gallery)
- [ ] W2.12 — `VoiceoverPanel.tsx` + `ReframeLayoutPicker.tsx` + `TransitionPicker.tsx`
- [ ] W2.13 — `FontUploader.tsx` + `VocabularyEditor.tsx`
- [ ] W2.14 — Wire `RIGHT_TOOLS` icons in editor

### Wave gate
- [ ] W2.15 — Full ladder + voiceover compose profile + tar-snapshot + deploy + SSE smoke

## Wave 3 — Collab / integrations

### Backend
- [ ] W3.1 — Migrations `l0…q5` (workspaces, members, folder hierarchy, scheduled_posts, analytics, share_links, webhooks, api_tokens) + nullable `workspace_id` FKs
- [ ] W3.2 — `scheduler_service.py` (Postgres `SKIP LOCKED`) + `scheduler_worker.py`
- [ ] W3.3 — `analytics_service.py` (per-platform Insights pull)
- [ ] W3.4 — `share_link_service.py` (HMAC-signed TTL)
- [ ] W3.5 — `webhook_dispatcher.py`
- [ ] W3.6 — `api_token_service.py` (bcrypt + constant-time match)
- [ ] W3.7 — `bulk_export.py` router
- [ ] W3.8 — Auth stubs in `app/auth.py` (`current_user_id` + bearer-token resolution)
- [ ] W3.9 — `capabilities.py` flag map
- [ ] W3.10 — pyannote diarisation + speaker-coloured captions

### Frontend
- [ ] W3.11 — `team.tsx`, `calendar.tsx`, `analytics.tsx`
- [ ] W3.12 — `settings.api.tsx`, `settings.webhooks.tsx`
- [ ] W3.13 — `share.$token.tsx` (public read-only)
- [ ] W3.14 — `useAutoSave.ts` hook
- [ ] W3.15 — Drop `placeholder: true` from sidebar; remove "Download 4K"

### Wave gate
- [ ] W3.16 — Full ladder + worker compose service + tar-snapshot + deploy + SSE smoke

---

## Review section

_Populated as PRs land. Each entry: PR# · summary · files touched · gate result._
