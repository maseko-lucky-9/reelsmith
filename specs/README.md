# Specs

Feature specifications follow the Spec Kit workflow: `/speckit-specify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement`, with one `specs/NNN-<slug>/` per feature (constitution principle VIII, [`.specify/memory/constitution.md`](../.specify/memory/constitution.md)).

Spec numbers and ADR numbers (`docs/decisions/NNN-*.md`) are separate sequences. For example, spec 005 (webhooks) has nothing to do with ADR-005 (route prefix).

## Index

| Spec | State | Documents |
|---|---|---|
| 001 Reelsmith baseline | Accepted; inventory kept current through PR #54 | [spec](001-reelsmith-baseline/spec.md) · [plan](001-reelsmith-baseline/plan.md) · [tasks](001-reelsmith-baseline/tasks.md) |
| 002 caption-brand-pipeline | Roadmap stub (from T013) | this file |
| 003 analytics | Roadmap stub (from T017) | this file |
| 004 share-links | Roadmap stub (from T017) | this file |
| 005 webhooks | Roadmap stub (from T017) | this file |
| 006 api-tokens | Roadmap stub (from T017) | this file |
| 007 workspaces | Roadmap stub (from T017) | this file |

## Roadmap stubs

These are not specs yet. Each stub records the user value, what is already in the repository, what is missing and what must come first. Writing the real spec starts with `/speckit-specify` and creates `specs/00N-<slug>/`. None of these features runs today: the baseline tags them `Scaffolded-unwired` (code) or `Missing` (HTTP API).

### 002-caption-brand-pipeline

From T013, FR-023.

- **User value.** Reels carry the creator's look and voice without hand editing: animated caption styles, transitions, the brand template (font, colours, caption style), brand-vocabulary spelling, a profanity filter and an optional voice-over.
- **Already in the repo.**
  - Services with unit tests: `app/services/animated_caption_service.py` (six presets), `transition_service.py`, `brand_vocabulary_service.py`, `profanity_filter_service.py` and `voiceover_service.py` (Coqui, Piper, stub).
  - Tables: `brand_templates` (its `caption_style` and `vocabulary` columns), `brand_template_fonts`, `caption_styles`, and `clips.caption_style`. The brand-template CRUD API (FR-030) and `POST /jobs` accept a `brand_template_id`.
  - UI: `web/src/routes/settings.brand.tsx` and `settings.captions.tsx` (with `CaptionTemplatePicker`) are labelled "Not applied to renders yet" (`web/src/components/not-applied-badge.tsx`). `TransitionPicker.tsx`, `VocabularyEditor.tsx` and `ReframeLayoutPicker.tsx` in `web/src/components/editor/` are mounted on no route.
- **Missing.** The orchestrator imports none of these services: see the `TODO(orchestrator-wiring-wave-2)` block in `_process_chapter`, `app/workers/orchestrator.py`. A job's `brand_template_id` is stored but never read by a render. The brand-template API does not expose `vocabulary`. `VOICEOVER_GENERATED`, `ANIMATED_CAPTION_RENDERED`, `TRANSITIONS_APPLIED` and `BRAND_VOCAB_APPLIED` are never emitted.
- **Prerequisite.** Every addition must keep the one-pass render and its caption rules (ADR-004, FR-007), so each feature needs an insertion point decided per stage. Voice-over needs a TTS model on the host: `YTVIDEO_PIPER_MODEL` for Piper. The Coqui path's `YTVIDEO_COQUI_MODEL`, named in the service docstring, is not a declared setting. This stub can be split into one spec per feature.

### 003-analytics

From T017.

- **User value.** See how published clips perform (impressions, views, watch time, likes, comments, shares) per platform.
- **Already in the repo.**
  - `app/services/analytics_service.py` (`record_snapshot`, `latest_per_platform`, `aggregate_for_clip`).
  - Table `clip_analytics_snapshots` (`ClipAnalyticsSnapshot`).
  - `web/src/routes/analytics.tsx`, hidden from the sidebar. It shows three counts computed from `GET /clips`, not analytics.
- **Missing.** Nothing collects snapshots from a platform, no route exposes them, and `ANALYTICS_REFRESHED` is never emitted.
- **Prerequisite.** Live publishing with real platform accounts: today only YouTube has a real adapter and `YTVIDEO_SOCIAL_PROVIDER` defaults to `stub` (FR-031). Each platform's insights API needs its own access.

### 004-share-links

From T017.

- **User value.** Send a reviewer a signed, expiring, revocable link to watch one clip without an account.
- **Already in the repo.**
  - `app/services/share_link_service.py` (HMAC `rs.<payload>.<sig>` tokens, `create_link`, `verify_token`, `revoke`).
  - Table `share_links` (`ShareLink`) and the `YTVIDEO_SHARE_LINK_SECRET` setting.
  - `web/src/routes/share.$token.tsx`, which says share links are not available yet.
- **Missing.** Routes to create, resolve and revoke a link, and a public player page. `SHARE_LINK_CREATED` is never emitted.
- **Prerequisite.** Decide how a public route sits beside `YTVIDEO_REQUIRE_AUTH` (FR-060). A stable `YTVIDEO_SHARE_LINK_SECRET` is required: unset, the service uses a per-process secret and links stop verifying after a restart.

### 005-webhooks

From T017.

- **User value.** Let n8n or another tool react when a job completes or a clip is published, instead of polling.
- **Already in the repo.**
  - `app/services/webhook_dispatcher.py` (HMAC-SHA256 signature, a retry budget on 5xx, no retry on 4xx).
  - Table `webhooks` (`Webhook`, with an encrypted secret).
  - `web/src/routes/settings.webhooks.tsx`, which says webhooks are not available yet.
- **Missing.** Routes to register and list webhooks. Nothing subscribes the dispatcher to the event bus, and `WEBHOOK_DISPATCHED` is never emitted.
- **Prerequisite.** Choose the events to deliver and where delivery runs (a bus subscriber in the API process, like the orchestrator). The n8n hand-off contract in `docs/social-publish-handoff.md` is the existing integration point.

### 006-api-tokens

From T017.

- **User value.** Script Reelsmith with named, revocable tokens instead of the single shared `YTVIDEO_API_KEY`.
- **Already in the repo.**
  - `app/services/api_token_service.py` (`mint_token`, bcrypt `hash_token`, `create_token`, `authenticate`, `revoke`) and table `api_tokens` (`ApiToken`, scoped to a workspace).
  - `app/auth.py` `current_user_id` already resolves a bearer token through it when `YTVIDEO_AUTH_ENABLED=true`. Only `POST /social/tiktok/connect` depends on it.
  - `web/src/routes/settings.api.tsx` shows a Python one-liner instead of a form.
- **Missing.** Routes and UI to issue, list and revoke tokens. There is also no single auth model: `YTVIDEO_REQUIRE_AUTH` (one key on every route) and `YTVIDEO_AUTH_ENABLED` (tokens, one route) are independent switches.
- **Prerequisite.** Decide the auth model first, including whether `/docs`, `/redoc` and `/openapi.json` stay open (FR-060, task T043). Tokens carry a `workspace_id`, so this spec depends on 007 or must fix the workspace to `local`.

### 007-workspaces

From T017.

- **User value.** Several people share one Reelsmith with roles (owner, member) and see only their workspace's jobs and clips.
- **Already in the repo.**
  - Tables `workspaces` and `workspace_members` (`Workspace`, `WorkspaceMember`), with no service and no route.
  - `app/auth.py` `current_workspace_id`, which returns the user id, `local` in single-tenant mode.
  - `app/services/capabilities.py` (tier flag map, always the BUSINESS tier; used only by its tests).
  - `web/src/routes/team.tsx`, a static "single-tenant mode" page.
- **Missing.** Membership and role management, and a workspace column on `jobs`, `clips` and `brand_templates`: today only `social_accounts.owner_id` and `api_tokens.workspace_id` carry an owner. Every query would also need a workspace filter.
- **Prerequisite.** Additive migrations on both engines (constitution V) and the auth model from 006. This is the largest stub; consider whether a single-user tool needs it at all.

## Not on the roadmap

- **Scheduled publishing** was dropped by owner decision (FR-032, T010). The `/calendar` page, `app/services/scheduler_service.py` and the `scheduled_posts` table remain; cleaning them up is task T045.
- **Speaker diarisation and speaker-coloured captions** (W3.10) were deferred in ADR-003 §A.15 (`tasks/todo.md`).
