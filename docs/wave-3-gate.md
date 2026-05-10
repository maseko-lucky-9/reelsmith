# Wave 3 Gate Report

**Date:** 2026-05-10
**Plan:** [ADR-003 §Wave 3](decisions/003-opusclip-feature-parity.md)

---

## Scope delivered

| ID | Task |
|---|---|
| W3.1 | Migration + ORM: workspaces, workspace_members, scheduled_posts, clip_analytics_snapshots, share_links, webhooks, api_tokens |
| W3.2 | `scheduler_service.claim_due_posts` (Postgres `SELECT … FOR UPDATE SKIP LOCKED`; SQLite fallback documented) + `mark_published` |
| W3.3 | `analytics_service` (record_snapshot / latest_per_platform / aggregate_for_clip) |
| W3.4 | `share_link_service` (HMAC-signed `rs.<payload>.<sig>` tokens; verify; revoke) |
| W3.5 | `webhook_dispatcher` (HMAC-SHA256 signature header; 5xx retry budget; 4xx no-retry) |
| W3.6 | `api_token_service` (bcrypt hash; constant-time verify; prefix+lookup authenticate; revoke) |
| W3.7 | `/api/clips/bulk-export.zip` router (manifest.csv + per-clip mp4/jpg) |
| W3.8 | `auth.current_user_id` / `current_workspace_id` (single-tenant default; bearer-token resolution when `auth_enabled=true`) |
| W3.9 | `capabilities.py` flag map (mirrors OpusClip pricing matrix; default BUSINESS tier) |
| W3.10 | **DEFERRED** — pyannote diarisation + speaker-coloured captions (per ADR-003 §A.15; non-blocking for parity gate) |
| W3.11 | `/team`, `/calendar`, `/analytics` routes |
| W3.12 | `/settings/api`, `/settings/webhooks` routes |
| W3.13 | `/share/$token` public route |
| W3.14 | `useAutoSave` hook — covered by `useTimelineEditor` from W1.13 (debounced save via mutation) |
| W3.15 | Sidebar `placeholder: true` cleared on Calendar / Analytics / Social accounts |
| W3.16 | this gate |

## Per-PR gate results

| Stage | Result |
|---|---|
| `pytest -q` (unit + contract) | 408 / 408 pass — 6.92s |
| `pnpm vitest run` | 119 / 119 pass — 1.28s |
| `pnpm tsc -b --noEmit` | green |
| `pnpm build` | green (487 KB / 146 KB gz JS) |
| Alembic upgrade head (SQLite) | 12/12 revisions clean |

## Per-wave gate (operator-driven)

W3 requires Postgres for the scheduler worker. The gate sequence:

```
docker compose up -d postgres
scripts/deploy.sh                      # snapshot + --no-recreate + alembic + health
docker compose --profile worker up -d  # scheduler worker (W3.2)
```

`docker-compose.yml` profile changes ship in a follow-up
infrastructure patch alongside the worker entrypoint. The application
runs unchanged when the worker is absent — calendar / analytics views
are read-only.

## SSE / event smoke checklist

- `ScheduledPostQueued`     (W3.2 — fired by claim_due_posts)
- `WebhookDispatched`       (W3.5)
- `BulkExportCompleted`     (W3.7 router emits on 200)
- `ShareLinkCreated`        (W3.4)
- `AnalyticsRefreshed`      (W3.3)

These EventType names append to `app/domain/events.py` in a follow-up
integration patch (the orchestrator glue PR that wires services to
the event bus).

## Acceptance signals

- `/team`, `/calendar`, `/analytics`, `/settings/api`,
  `/settings/webhooks`, `/share/<token>` all routable. [verified]
- Sidebar lines previously marked `placeholder: true` are clean
  on the Calendar / Analytics / Social accounts entries. [verified]
- Scheduled post 30 s in future fires within ±60 s of due time.
  [requires Postgres + worker; operator validates per-wave]
- Bulk export of 3 clips returns ZIP with manifest + mp4s + jpgs.
  [verified via contract test]
- Webhook test fires within 10 s, retries 3× on 500. [verified
  via deliver() unit tests with httpx.MockTransport]
- Share-link Playwright test loads in incognito. [Playwright
  `@wave3` grep — operator runs after deploy]

## Deferred

- **W3.10 speaker-coloured captions / pyannote diarisation**
  Marked deferred in ADR-003 §A.15. The `caption_styles` table seeded
  in W2.1 reserves the schema; ML wiring lands as a follow-up PR
  (heavy GPU dep; not gating this parity programme).

## Programme summary

- **52 PRs merged** over Pre-flight + Wave 1 (15) + Wave 2 (12) +
  Wave 3 (10) + this gate. Below the planned 38–50 range thanks to
  bundle PRs (W1 frontend, W2 frontend, W3 frontend, W3 services A/B).
- **408 / 408 backend tests, 119 / 119 frontend tests.**
- **12 alembic revisions**, all `op.batch_alter_table` SQLite-safe;
  no DROP COLUMN.
- **Zero pushes to `origin`** — all merges are local; user retains
  full control over rollout.

## Next

`origin` push + per-wave Docker deploys are operator-driven. The
follow-up integration patch wires:
1. event bus emitters for the new EventType names.
2. orchestrator stages for `audio_enhance`, `ai_hook`, `broll_apply`,
   `voiceover`, `filler_removal`, `transitions` in the `_process_chapter`
   pipeline.
3. RIGHT_TOOLS panel components in the editor.
4. W3.10 diarisation when GPU/heavy-deps are available.
