# Wave 2 Gate Report

**Date:** 2026-05-10
**Plan:** [ADR-003 §Wave 2](decisions/003-opusclip-feature-parity.md)

---

## Scope delivered

| ID | Task |
|---|---|
| W2.1 | Animated caption presets (static/hormozi/mrbeast/karaoke/boldpop/subtle) + `caption_styles` table |
| W2.2 | `active_speaker_service` (smooth_cues + split-screen heuristic) |
| W2.3 | `voiceover_service` (Coqui XTTS / Piper / stub) + WAV header generator |
| W2.4 | `audio_enhance_service` `demucs` provider (opt-in) |
| W2.5 | `filler_removal_service` (lexicon + silence-gap coalescing) |
| W2.6 | `transition_service` (fade / slide-l/r / zoom xfade argv) |
| W2.7 | `brand_vocabulary_service` (caption-time replacement; case-preserving) |
| W2.8 | `brand_template_fonts` table (multi-font per template) |
| W2.9 | `profanity_filter_service` (default / custom / off) |
| W2.10 | SSE `with_heartbeat` + Postgres `pool_recycle` + `stage_timeout_seconds` |
| W2.11 | `CaptionTemplatePicker` + `/settings/captions` route |
| W2.12 | `ReframeLayoutPicker` (replaces aspect-only dropdown) |
| W2.13 | `TransitionPicker` |
| W2.14 | `VocabularyEditor` |
| W2.15 | this gate |

## Per-PR gate results

| Stage | Result |
|---|---|
| `pytest -q` (unit + contract) | 370 / 370 pass — 5.07s |
| `pnpm vitest run` | 119 / 119 pass — 1.25s |
| `pnpm tsc -b --noEmit` | green |
| `pnpm build` | green (481 KB / 144 KB gz JS) |
| Alembic upgrade head (SQLite) | 11/11 revisions clean (W2.1 + W2.7+8 added) |

## Per-wave gate (operator-driven)

`scripts/deploy.sh` runs unchanged from W1. Wave 2 also requires:

```
docker compose --profile voiceover up -d
```

…to start the optional Coqui XTTS service (~2 GB model). When the
profile is off, `voiceover_service` falls back to the deterministic
stub WAV.

## SSE smoke checklist (operator runs after deploy)

- `FillersRemoved`        (W2.5 stage)
- `VoiceoverGenerated`    (W2.3 — opt-in via PipelineOptions)
- `AnimatedCaptionRendered` (W2.1)
- `TransitionsApplied`    (W2.6)
- `BrandVocabApplied`     (W2.7)
- SSE stream stays alive past 15 s of stage idle (heartbeat, W2.10)

## Acceptance signals

- `/settings/captions` renders 6 cards. [verified]
- Caption-template picker fires onChange. [vitest]
- Reframe picker exposes 9:16 / 1:1 / 16:9 + 3 layouts. [verified]
- Vocabulary editor adds/removes entries. [vitest]
- Filler-removal shrinks fixture clip ≥ 0.4 s. [pytest]
- Profanity filter `default` mode bleeps; `off` mode passes through;
  `custom` mode preserves word boundaries. [pytest]

## Next

Wave 3 — collab + integrations: workspaces, folder hierarchy,
Postgres `SKIP LOCKED` scheduler (retires APScheduler), calendar +
analytics + share links + webhooks + REST API tokens + bulk export.
