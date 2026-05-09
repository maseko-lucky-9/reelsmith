# Reelsmith

**Problem:** Manually trimming long-form videos from YouTube, Facebook, TikTok, or Instagram into captioned short-form clips is tedious and time-consuming for a solo creator.

Reelsmith automates the pipeline: download a video from any supported platform, transcribe it with word-level timing, score clips by virality heuristics, burn karaoke subtitles, and produce 9:16 vertical reels — all through a FastAPI backend and a React dashboard with a live per-stage progress timeline.

## Supported Platforms

| Platform | Mode | Chapter support |
|---|---|---|
| YouTube | Long-form | Full chapter parsing |
| Facebook | Short-form | Single clip (no chapters) |
| TikTok | Short-form | Single clip (no chapters) |
| Instagram | Short-form | Public posts only |

URLs are routed via a `PlatformAdapter` strategy registry (`app/services/platforms/`). Unsupported URLs are rejected at submission time with HTTP 400.

## Quick Start

```bash
# 1. Start Postgres
docker compose up -d postgres

# 2. Install Python deps
python -m venv .venv-mac && source .venv-mac/bin/activate
pip install -r requirements.txt

# 3. Start API (memory store for dev)
YTVIDEO_JOB_STORE=memory uvicorn app.main:app --reload

# 4. Start React dev server (separate terminal)
cd web && pnpm install && pnpm dev
```

Open **<http://localhost:5173>** in your browser.

> **Note:** Only process videos you have the right to use. Check the platform's terms of service before downloading.

## Architecture

```
web/                     — React 19 + Vite + TanStack Router/Query + shadcn/ui
  components/job-progress-timeline.tsx — live per-stage progress UI on /jobs/$jobId
  components/platform-chip.tsx         — shared platform badge
  lib/pipelineStages.ts                — pure deriveStageStates(job, events) helper
  lib/detectPlatform.ts                — frontend mirror of the platform registry
app/main.py              — FastAPI app, job queue, SSE streaming
app/routers/             — jobs, clips, media, uploads, brand_templates
app/services/            — transcription, caption, render, segment_proposer, reframe, broll, thumbnail
app/services/platforms/  — PlatformAdapter strategy: youtube, facebook, tiktok, instagram
app/services/download_service.py — backward-compat shim (delegates to YouTube adapter)
app/workers/orchestrator — async pipeline runner (resolves adapter per URL)
app/domain/              — events, models (incl. JobState.source), IDs
app/bus/                 — async event bus + job store (memory / Postgres)
app/db/                  — SQLAlchemy ORM models + alembic migrations
```

## Live Progress Timeline

The `/jobs/$jobId` page renders a per-stage timeline while the pipeline runs. Stages: prepare workspace → download source → detect chapters → extract clips → transcribe → caption → render → thumbnails+social → export & manifest → done. Per-chapter stages show `N/M` sub-progress.

- **Data plane:** `useJobSSE` mirrors every SSE event into the React Query cache `['job-events', jobId]`. `deriveStageStates(jobState, events)` is a pure helper — `JobState` is the source of truth, events are a low-latency optimisation. Max-merge reconciliation between SSE counts and `JobState.chapters[i]` artifact fields means a stage never un-completes (kills SSE-reconnect drift and tab-refocus races in one rule).
- **Accessibility:** single visually-hidden `role="status" aria-live="polite"` region announces only stage transitions (~10/job, not ~60). Active row gets `aria-current="step"` plus a static emerald left-border so reduced-motion users still get a non-animation cue.
- **Resilience:** `<TimelineErrorBoundary>` wraps the component; a malformed `JobState` falls back without blanking the page.

## Stack

| Layer | Library |
|---|---|
| API | FastAPI + Uvicorn |
| Database | Postgres 16 + SQLAlchemy 2 async + Alembic |
| Video download | yt-dlp (YouTube / Facebook / TikTok / Instagram via PlatformAdapter registry) |
| Video editing | MoviePy |
| Transcription | Whisper (word-level) |
| Virality scoring | librosa + VADER + spaCy + webrtcvad |
| Reframe | MediaPipe face detection |
| Captions | pysrt / webvtt-py |
| Subtitle images | Pillow + NumPy |
| UI | React 19 + Vite 8 + shadcn/ui |
| Tests | pytest + vitest |

## Environment Variables

See `.env.example` for the full list. Key settings:

| Variable | Default | Description |
|---|---|---|
| `YTVIDEO_JOB_STORE` | `memory` | `memory` or `sql` |
| `YTVIDEO_DB_URL` | `postgresql+asyncpg://reelsmith:reelsmith@localhost/reelsmith` | Postgres connection |
| `YTVIDEO_SEGMENT_PROVIDER` | `chapter` | `chapter`, `local_heuristic`, or `stub` |
| `YTVIDEO_REFRAME_PROVIDER` | `letterbox` | `letterbox`, `face_track`, or `stub` |
| `YTVIDEO_SERVE_FRONTEND` | `false` | Serve built React app from FastAPI |
| `YTVIDEO_REQUIRE_AUTH` | `false` | Enable API key auth |
| `YTVIDEO_API_KEY` | `null` | API key when auth enabled |

## Testing

```bash
# Unit + contract tests (no network, no Postgres)
pytest

# Integration tests (requires Postgres)
YTVIDEO_DB_URL=postgresql+asyncpg://reelsmith:reelsmith@localhost:5432/reelsmith pytest -m integration

# Frontend tests
cd web && pnpm test

# Full build check
cd web && pnpm build
```

## Production Build

```bash
cd web && pnpm build
# Then run API with YTVIDEO_SERVE_FRONTEND=true
YTVIDEO_SERVE_FRONTEND=true uvicorn app.main:app
```
