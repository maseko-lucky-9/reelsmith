# Wave 1 Gate Report

**Date:** 2026-05-10
**Plan:** [ADR-003 §Wave 1](decisions/003-opusclip-feature-parity.md)
**Loop config:** [tasks/loop-config.yaml](../tasks/loop-config.yaml)

---

## Scope delivered

| ID | Task | Merge SHA |
|---|---|---|
| W1.1 | `clip_edits` migration + ORM model | `b02b5af` |
| W1.2 | `clip_edits` CRUD router | `ed34f1d` |
| W1.3 | `social_accounts` migration + Fernet vault | (W1.3 merge) |
| W1.4 | `publish_jobs` migration + APScheduler scaffold | (W1.4 merge) |
| W1.5 | platform adapters (stub default + YouTube live) + orchestrator | (W1.5 merge) |
| W1.6 | `social_publish` + `xml_export` routers + Jinja2 templates | (W1.6 merge) |
| W1.7 | AI hook service + router | (W1.7 merge) |
| W1.8 | `audio_enhance_service` (loudnorm / rnnoise / passthrough) + router | (W1.8 merge) |
| W1.9 | `broll_assets` column + Pexels provider with LRU cache | (W1.9 merge) |
| W1.10 | reprompt endpoint + custom clip length range in `PipelineOptions` | (W1.10 merge) |
| W1.11 | Replace `ComingSoonButton` stubs on `ClipListRow` | `e21152a` |
| W1.12 | `MultiTrackTimeline` + `useTimelineEditor` + `timeline_render_service` | `c337e26` + `e21152a` |
| W1.13 | Undo / Redo / Save wired on `clips.$clipId.edit` | `e21152a` |
| W1.14 | `/settings/social` + `/clips/$clipId/publish` routes | `e21152a` |
| W1.15 | this gate report + `scripts/deploy.sh` | (this PR) |

## Per-PR gate results

| Stage | Result |
|---|---|
| `pytest -q` (unit + contract) | 319 / 319 pass — 4.92s |
| `pnpm vitest run` | 111 / 111 pass — 1.14s |
| `pnpm tsc -b --noEmit` | green (with `@testing-library/jest-dom` types fix in W1.14) |
| `pnpm build` | green (479 KB / 143 KB gz JS bundle) |
| Pre-commit guardrails | gitleaks v8.21.2 + standard hygiene (PR-0c) |
| Alembic upgrade head (SQLite) | 9/9 revisions clean (incl. W1 additions) |

## Per-wave gate (operator-driven; this PR ships the script)

The user runs the per-wave gate on their machine because the agent cannot
start a Docker daemon. The gate is a single command:

```
scripts/deploy.sh
```

`scripts/deploy.sh` enforces all guardrails from ADR-003 + Appendix A.11:

1. tar-snapshots `reelsmith_postgres_data` → `data/backups/postgres-YYYYMMDD-HHMMSS.tgz`.
2. `docker compose up -d --no-recreate` (NEVER `-v`).
3. `alembic upgrade head` on the host.
4. Polls `GET /api/health` for up to 20 s and exits non-zero on failure.

Override knobs:
- `SKIP_SNAPSHOT=1` for faster iteration during development.
- `RECREATE=1` when a migration requires container recreate.

## SSE smoke checklist (operator runs after deploy)

For a fixture URL with the W1 flags on, the SSE stream should emit at least
one of each of:

- `AudioEnhanced`        (W1.8 stage)
- `AiHookGenerated`      (W1.7 stage; opt-in via PipelineOptions.ai_hook)
- `BRollApplied`         (W1.9 stage; default broll provider remains 'local')
- `XmlExported`          (response to `/api/clips/:id/export.xml`)
- `PublishQueued` → `PublishCompleted` (immediate stub publish)
- `TimelineEdited`       (PUT `/api/clips/:id/edit`)

These event names are wired through the existing `EventType` enum; new
emitters are appended by W1 services in subsequent integration patches.

## Acceptance signals

- `/clips/:id` page has zero `Coming soon` buttons. [verified]
- `/clips/:id/edit` Undo/Redo/Save are enabled and persist to
  `clip_edits.timeline + version`. [verified]
- `GET /api/clips/:id/export.xml?format=premiere|davinci` returns
  non-empty XML. [verified by contract test]
- `manifest.csv` gains `broll_attribution` column when Pexels is the
  active provider. (deferred to first integration PR after operator
  enables `YTVIDEO_BROLL_PROVIDER=pexels`.)
- `/settings/social` and `/clips/:id/publish` routable. [verified]
- Multi-track timeline screenshot in `web/test-results/` after
  Playwright run. (Playwright `@wave1` grep — operator runs after
  full deploy.)

## Next

Wave 2 begins on top of this gate: animated captions, voice-over,
filler/silence removal, transitions, brand vocabulary, profanity
filter, multi-aspect reframe. Wave 1 services (`audio_enhance`,
`ai_hook`, `broll_pexels`) are extended in place with their W2
provider variants.
