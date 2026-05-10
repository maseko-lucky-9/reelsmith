# ADR-003: OpusClip Feature Parity (Waves 1-3)

**Status:** Accepted
**Date:** 2026-05-10
**Author:** Thulani Maseko
**Supersedes:** Stub commitments in [ADR-002](002-opus-clip-ui-redesign.md)

## Context

ADR-002 replicated OpusClip's UI chrome but explicitly stubbed five product features ("Coming soon"): Publish on Social, Export XML, AI Hook, Add B-Roll, Enhance Speech. The clip editor was reduced to a transcript-panel-only surface with a disabled multi-track timeline.

Subsequent work (commit `5865534`) shipped `PipelineOptions`, the orchestrator stage-gating model, the frontend `STAGES` / `TOGGLES` registries, and the Advanced workflow tab. These now form the dispatch substrate that Waves 1-3 extend.

This ADR records the decision to deliver full OpusClip feature parity across three waves driven by the autonomous Ralph loop, gated per-PR by lint + fast tests + build, and per-wave by full test ladder + local Docker deploy + SSE smoke.

## Decision

Implement parity in three waves:

| Wave | Theme | New surface |
|---|---|---|
| W1 | Stub replacement + inline editor | AI Hook, AI B-Roll (Pexels), Enhance Speech (loudnorm/RNNoise), Publish on Social (YouTube real + 4 stub adapters), Export XML (Premiere FCP7 + DaVinci FCPXML), inline multi-track editor backed by `clip_edits` table |
| W2 | AI quality + reframe | Animated caption templates, multi-aspect reframe, Coqui XTTS voice-over, demucs (opt-in), filler/silence removal, transitions, custom fonts, brand vocabulary, profanity filter |
| W3 | Collab / integrations | Workspace + roles, folder hierarchy, Postgres-backed scheduler, calendar, analytics, share links, webhooks, REST API tokens, bulk export, multi-profile per platform |

Single-tenant: tier matrix replicated as `capabilities.py` flag map; everything defaults to "unlocked" until a future auth wave (W3 stubs `current_user_id() -> "local"`).

## Operating principles

1. Backward-compatible migrations only (additive nullable columns; `op.batch_alter_table` for SQLite parity).
2. Provider-pluggable services follow the existing `<real>|stub` pattern.
3. `YTVIDEO_` env-var prefix; `EventType` enum is append-only.
4. Pipeline stages additive via `PipelineOptions` flags + cascade safety net in orchestrator.
5. Small green PRs; PR N+1 blocked until PR N green on fast suite.
6. Per-PR gate: lint + fast pytest + vitest + build → merge. Per-wave gate: full ladder + `docker compose up -d --no-recreate` + `alembic upgrade head` + SSE smoke.
7. Never `docker compose down -v`. Tar-snapshot Postgres volume before each wave deploy.
8. Coqui XTTS v2 (CPML non-commercial) accepted for the learning/portfolio scope of this repo. If commercialised, swap to Piper / paid TTS.

## Cross-cutting decisions (locked)

| # | Decision | Pick | Why |
|---|---|---|---|
| 1 | Stage insertion mechanism | Append `bool` flag to `PipelineOptions`, gate in orchestrator, append `StageDescriptor` and `ToggleDescriptor` to frontend registries | Substrate already shipped in `5865534`; no refactor needed |
| 2 | Multi-track timeline state | New `clip_edits` table; `timeline JSON` schema; server-side `timeline_render_service` (MoviePy CompositeVideoClip) | WYSIWYG with final export; rejects WebCodecs/ffmpeg.wasm |
| 3 | Editor preview | 240p server-side proxy render | Pixel-true vs final |
| 4 | Social OAuth tokens | Fernet-encrypted at rest; lazy refresh on use; `SELECT ... FOR UPDATE` to serialise refreshes | Standard, no extra infra |
| 5 | Scheduler | W1 APScheduler scaffold → W3 Postgres-backed worker (`SKIP LOCKED`) | Postgres survives restarts; calendar UI reads same table |
| 6 | Captions render | Server-side burn-in via animated_caption_service → transparent MOV overlay | WASM ffmpeg unreliable |
| 7 | Pexels cache | FS `data/broll-cache/`; LRU 5 GB; 7d TTL on search | Pexels free tier = 200 req/hr |
| 8 | Speech enhance | W1 ffmpeg `loudnorm` + RNNoise; W2 demucs opt-in | loudnorm = highest-impact baseline |
| 9 | Voice-over | Coqui XTTS v2 (CPML; portfolio scope) | Multi-voice + cloning + multi-lingual |
| 10 | Tier-gating | `capabilities.py` flag map; default `business` | Single-tenant; future auth flips return value only |

## Scope adjustments (from adversarial + post-`5865534` review)

- **W1 social adapters**: Stub provider as default + YouTube real adapter only. TikTok / IG / LinkedIn / X = OAuth scaffold + adapter shell + `docs/social-publish-onboarding.md` checklist. Not gated on app review.
- **Pexels**: opt-in via env var; default `broll_provider=local` (existing matcher).
- **Hook-points refactor**: dropped — `PipelineOptions` substrate from `5865534` is sufficient.
- **react-rnd**: dropped — `@dnd-kit/core` + `@dnd-kit/modifiers` only.
- **Reprompt + custom clip length range**: added to W1.
- **Speaker-coloured captions**: deferred to W3 (paired with diarisation).
- **Profanity filter**: added to W2.
- **Long stages**: SSE keep-alive heartbeat (`: ping\n\n` every 15s), `pool_pre_ping=True`, `pool_recycle=1800`, `YTVIDEO_STAGE_TIMEOUT_SECONDS=1800`.
- **Auto-merge guardrail**: gitleaks pre-commit + `.gitignore` audit.

## Self-approval deviation (CLAUDE.md)

The user-authorised "code + auto-merge + deploy" loop deviates from the global rule "Keep authoring and review as separate passes; never self-approve in the same active context." Mitigations:

1. All Ralph PRs auto-tagged `ralph-autonomous` for grep/audit.
2. `mcp__gemini-mcp__gemini_code_review` runs as an out-of-context reviewer pass before merge — independent eye, no human block.
3. Per-wave deploy gate held by user (not Ralph) for waves 1-3.

## Consequences

- Editor surface ([web/src/routes/clips.$clipId.edit.tsx](../../web/src/routes/clips.$clipId.edit.tsx)) gains real Undo/Redo/Save backed by `clip_edits` API and `useTimelineEditor` hook.
- Sidebar entries ([web/src/components/layout/Sidebar.tsx:30-34](../../web/src/components/layout/Sidebar.tsx)) lose `placeholder: true` after W3 lands.
- "Download 4K" stub on [web/src/components/dashboard/ClipListRow.tsx:166](../../web/src/components/dashboard/ClipListRow.tsx) is removed (out-of-scope; opt-in Real-ESRGAN deferred).
- ~14 new tables, ~18 migrations, ~20 new services, ~17 new FE components, ~10 new FE routes.
- Estimated ~42 PRs (range 38-50).

## Verification per wave

```
1. ruff check app/ tests/ && (mypy app/ if wired)
   pnpm --dir web lint && pnpm --dir web tsc -b
2. pytest -q                                          # fast
3. docker compose up -d postgres redis
   pytest -m integration -q
4. pytest -m e2e -k "waveN" -q
5. pnpm --dir web vitest run
6. pnpm --dir web playwright test --grep "@waveN"
7. pnpm --dir web build
8. Pre-deploy: tar snapshot of postgres volume → data/backups/$(date +%Y%m%d-%H%M).tgz
   docker compose up -d --no-recreate                 # NEVER -v
   alembic upgrade head
   curl -fsS http://localhost:8000/api/health
   curl -N http://localhost:8000/api/jobs/<id>/events | grep -E "<wave events>"
9. (W2) docker compose --profile voiceover up -d
10. (W3) docker compose logs -f worker | grep "picked scheduled_post"
```

## References

- Plan: `~/.claude/plans/reelsmith-if-missing-functionalities-compiled-llama.md`
- URP report: [docs/research/opusclip-parity-urp.md](../research/opusclip-parity-urp.md)
- Task list: [tasks/todo.md](../../tasks/todo.md)
- Predecessor: [docs/decisions/002-opus-clip-ui-redesign.md](002-opus-clip-ui-redesign.md)
