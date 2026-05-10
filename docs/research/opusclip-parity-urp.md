# OpusClip Parity — Universal Research Pipeline Report

**Date:** 2026-05-10 · **Author:** Thulani Maseko
**Repo:** `/Users/ltmas/Repo/experiments/learning/python/reelsmith`
**Context:** Backing research for [ADR-003](../decisions/003-opusclip-feature-parity.md)

---

## 1. Configuration

| Source class | Method | Notes |
|---|---|---|
| OpusClip product surface | Public Playwright capture (`opus.pro/`, `opus.pro/pricing`) | No paid creds; reverse-engineered tier matrix from pricing page |
| ReelSmith codebase | `Read` + `grep` against `app/`, `web/src/` at HEAD `5865534` | Inventory of routers, services, ORM models, FE routes, FE registries |
| Predecessor ADR | [docs/decisions/002-opus-clip-ui-redesign.md](../decisions/002-opus-clip-ui-redesign.md) | Ground-truth for stubbed surface |
| Repo screenshots (`/screenshots/`) | 32 manually captured OpusClip frames | UX detail (caption template animations, timeline, scheduler) |

Excluded: paid-tier OpusClip surfaces (no creds), web-search/StackOverflow citations (out of scope), competitor-tool comparison.

## 2. Findings — OpusClip canonical features

Captured from `opus.pro/pricing` and product pages. Tier-gating noted but not enforced in ReelSmith (single-tenant).

### 2.1 Editing & quality
- **ClipAnything**: AI clip selection from a long-form input.
- **ReframeAnything**: 9:16 / 1:1 / 16:9; active-speaker tracking; split-screen / screenshare.
- **AI Captions**: animated templates (Hormozi, MrBeast, Karaoke, BoldPop, Subtle), emoji, keyword highlights, speaker-based colours.
- **AI B-Roll**: stock-footage match against transcript noun phrases.
- **Speech enhancement**: noise reduction, loudness normalisation, voice isolation.
- **AI Voice-over**: 17+ voices, zero-shot cloning, multi-lingual.
- **Filler & silence removal**: VAD + lexicon + POS.
- **Transitions**: fade / slide / zoom.
- **Brand templates**: logo, fonts, colours, intro/outro.
- **Brand vocabulary glossary**: caption-time replacement.
- **Profanity filter**: default + custom word list.
- **Custom clip length**: `0-1m | 1-3m | 3-5m | 5-10m | 10-15m`.
- **Reprompt clipping**: re-run segment selection with new prompt.

### 2.2 Distribution
- **Publish on Social**: YouTube, TikTok, IG Reels, LinkedIn, X.
- **Multi-profile per platform.**
- **Scheduler**: calendar; bulk schedule.
- **Export XML**: Premiere Pro FCP7 + DaVinci Resolve FCPXML.
- **Bulk export** (ZIP).
- **Share links**: public read-only.

### 2.3 Collaboration & integrations
- **Workspace + roles** (owner / editor / viewer).
- **Folder hierarchy + sub-teams.**
- **Auto-save** (debounced).
- **Clip analytics** (impressions / views / watch time per platform).
- **Webhooks**: outbound on job/clip/publish events.
- **REST API tokens.**
- **Title / description / hashtag generator**: A/B variants.

### 2.4 UI affordances
- **Inline multi-track editor** with trim/cut/text-overlay/zoom.
- **Auto-save** (debounced 2s + visibility-flush).
- **Undo / Redo / Save** wired to timeline state.
- **Caption-template gallery.**
- **Reframe layout picker** (replaces aspect dropdown).
- **Vocabulary editor.**

## 3. Findings — ReelSmith inventory at `5865534`

### 3.1 Already shipped
- `PipelineOptions` Pydantic submodel ([app/domain/models.py:11-18](../../app/domain/models.py)) — 7 stage flags.
- Orchestrator gating + `StageSkipped` events ([app/workers/orchestrator.py:79-84, 241-243, 318-331](../../app/workers/orchestrator.py)).
- Frontend `STAGES` registry ([web/src/lib/pipelineStages.ts](../../web/src/lib/pipelineStages.ts)) and `TOGGLES` registry ([web/src/lib/pipelineToggles.ts](../../web/src/lib/pipelineToggles.ts)).
- `AdvancedOptionsPanel` ([web/src/components/advanced-options-panel.tsx](../../web/src/components/advanced-options-panel.tsx)) — iterates `TOGGLES` dynamically.
- 8-platform e2e fixtures ([web/tests/e2e/fixtures/urls.ts](../../web/tests/e2e/fixtures/urls.ts)).
- Cross-platform thumbnail proxy `/jobs/preview/thumbnail` (closes IG/FB referer-403 gap).
- Playwright retries: `retries: process.env.CI ? 2 : 0` ([web/playwright.config.ts:7](../../web/playwright.config.ts)).
- Migration parity via `op.batch_alter_table` (SQLite-safe pattern).

### 3.2 Still stubbed (ADR-002 "Coming soon")
- `<ComingSoonButton>` clusters at [web/src/components/dashboard/ClipListRow.tsx:142-166](../../web/src/components/dashboard/ClipListRow.tsx) — Publish, Export XML, AI Hook, Enhance Speech, B-Roll.
- Editor disabled controls at [web/src/routes/clips.$clipId.edit.tsx:138-153, 183-188, 280-307](../../web/src/routes/clips.$clipId.edit.tsx) — Undo/Redo/Save, Add a section, single-input scrubber (no multi-track).
- Sidebar `placeholder: true` flags at [web/src/components/layout/Sidebar.tsx:30-34](../../web/src/components/layout/Sidebar.tsx) — Calendar, Analytics, Social accounts.
- Workflow page social-account link at [web/src/routes/workflow.tsx:199, 395](../../web/src/routes/workflow.tsx).

### 3.3 Existing utilities to reuse
- `app/services/transcription_service.py` — provider pattern template (`whisper`/`stub`).
- `app/services/reframe_service.py` — `face_track` baseline; W2 adds `active_speaker`.
- `app/services/broll_service.py` — local noun-phrase matcher; W1 adds Pexels as alternate provider.
- `app/services/ollama_service.py` — wrapped by W1 `ai_hook_service`.
- `app/services/render_service.py` — main pipeline render; W1 adds sibling `timeline_render_service` for editor renders.
- `app/services/manifest_service.py` — CSV manifest; W1 adds `broll_attribution` column.
- `app/services/caption_service.py` — SRT/VTT generator; W2 wraps for animated captions.

## 4. Classification (gap map)

| OpusClip feature | ReelSmith status | Wave |
|---|---|---|
| ClipAnything | Shipped (segment proposer) | — |
| ReframeAnything (multi-aspect + active speaker) | Partial (`face_track` only) | W2 |
| AI Captions: static | Shipped | — |
| AI Captions: animated templates | Stub | W2 |
| Speaker-coloured captions | Not started | W3 (with diarisation) |
| AI B-Roll (Pexels) | Stub (local matcher only) | W1 |
| Speech enhancement | Stub | W1 (loudnorm/RNNoise) + W2 (demucs) |
| AI Voice-over | Not started | W2 (Coqui XTTS v2) |
| Filler / silence removal | Not started | W2 |
| Transitions | Not started | W2 |
| Brand templates | Shipped | — |
| Custom fonts (≥2 per template) | Not started | W2 |
| Brand vocabulary | Not started | W2 |
| Profanity filter | Not started | W2 |
| Custom clip length range | Partial | W1 |
| Reprompt clipping | Not started | W1 |
| Publish on Social | Stub | W1 (YouTube real + 4 stubs) |
| Multi-profile per platform | Not started | W3 |
| Scheduler / calendar | Sidebar placeholder | W1 scaffold + W3 Postgres |
| Bulk schedule | Not started | W3 |
| Export XML | Stub | W1 |
| Bulk export ZIP | Not started | W3 |
| Share links | Not started | W3 |
| Workspace + roles | Not started | W3 |
| Folder hierarchy | Partial (flat folders) | W3 |
| Auto-save | Not started | W3 |
| Clip analytics | Sidebar placeholder | W3 |
| Webhooks | Not started | W3 |
| REST API tokens | Not started | W3 |
| Inline multi-track editor | Stub | W1 |
| Editor Undo/Redo/Save | Stub | W1 |

## 5. Recommendations

1. **Honour the substrate.** Do not refactor `PipelineOptions` / `STAGES` / `TOGGLES`. Append-only.
2. **Sequence:** pre-flight (PR-0a-0e) → W1 → W2 → W3. Each wave gated by full ladder + local Docker deploy.
3. **Defer:** speaker-coloured captions (W3 with pyannote), Real-ESRGAN upscaling (out of scope), voice-cloning UI (post-W3).
4. **Risk hot-spots:** OAuth verification (TikTok / IG / LinkedIn / X — app review weeks); Coqui licence (CPML, non-commercial only); long-stage SSE drift (heartbeat + pool config).
5. **Always tar-snapshot Postgres** before each wave deploy; never `docker compose down -v`.

## 6. Acceptance signals (per wave)

- **W1 done when:** clip detail page has zero `Coming soon` buttons; `/clips/<id>/edit` trim+save+reload persists; Export XML downloads non-empty `.xml` / `.fcpxml`; `manifest.csv` gains `broll_attribution` column; SSE stream contains W1 events; Playwright screenshot of multi-track timeline committed.
- **W2 done when:** caption-template gallery renders 5 cards; clip with `animation_kind=hormozi` shows pop-in (frame-diff vs static); fixture clip with 5 known "um"s shrinks ≥0.4s; voiceover POST returns WAV ≥1KB; brand template carries 2 fonts; W2 events emitted.
- **W3 done when:** `/team`, `/calendar`, `/analytics`, `/settings/api`, `/settings/webhooks`, `/share/<token>` routable; scheduled post 30s in future fires within ±60s; bulk export of 3 clips returns ZIP with manifest+mp4s+jpgs; webhook test fires within 10s, retries 3× on 500; sidebar lines 30-34 free of `placeholder: true`.
