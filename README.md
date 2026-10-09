# Reelsmith

**Problem:** Manually trimming long-form videos from YouTube, Facebook, TikTok, or Instagram into captioned short-form clips is tedious and time-consuming for a solo creator.

Reelsmith automates the pipeline: download a video from any supported platform, transcribe it with word-level timing, cut chapters into clips (or, with `YTVIDEO_SEGMENT_PROVIDER=local_heuristic`, pick clips from a source without chapters by heuristic scores), burn karaoke subtitles, and produce 9:16 vertical reels — all through a FastAPI backend and a React dashboard with a live per-stage progress timeline.

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

# 2. Install Python deps (Python 3.14 required; 3.12 is unsupported, see CLAUDE.md)
python3.14 -m venv .venv-mac && source .venv-mac/bin/activate
pip install -r requirements.txt

# 3. Start API (memory store for dev)
YTVIDEO_JOB_STORE=memory uvicorn app.main:app --reload

# 4. Start React dev server (separate terminal)
cd web && pnpm install && pnpm dev

# 5. Install the git hooks (once per clone)
pip install pre-commit && pre-commit install
```

`pre-commit install` wires `.pre-commit-config.yaml` into `.git/hooks/pre-commit`, so every commit runs gitleaks on the staged changes (using `.gitleaks.toml`) plus basic hygiene checks (large files, merge markers, YAML, private keys). A detected secret blocks the commit. CI runs the same gitleaks scan in the `secrets` job.

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

- **Live updates, no polling:** the backend sends named SSE events, and `useJobSSE` registers a listener for every backend event type. Each event refreshes only that job and its clips. The job page has no `refetchInterval`, and the stream is open only while the job is `pending`/`running`.
- **Data plane:** `useJobSSE` mirrors every SSE event into the React Query cache `['job-events', jobId]`. `deriveStageStates(jobState, events)` is a pure helper — `JobState` is the source of truth, events are a low-latency optimisation. Max-merge reconciliation between SSE counts and `JobState.chapters[i]` artifact fields means a stage never un-completes (kills SSE-reconnect drift and tab-refocus races in one rule).
- **Accessibility:** single visually-hidden `role="status" aria-live="polite"` region announces only stage transitions (~10/job, not ~60). Active row gets `aria-current="step"` plus a static emerald left-border so reduced-motion users still get a non-animation cue.
- **Resilience:** `<TimelineErrorBoundary>` wraps the component; a malformed `JobState` falls back without blanking the page.

## Stack

| Layer | Library |
|---|---|
| API | FastAPI + Uvicorn |
| Database | Postgres 16 + SQLAlchemy 2 async + Alembic |
| Video download | yt-dlp (YouTube / Facebook / TikTok / Instagram via PlatformAdapter registry) |
| Video editing | ffmpeg (bundled via imageio-ffmpeg) + PyAV |
| Transcription | faster-whisper (word-level) |
| Segment scoring | NumPy + standard library (wav RMS, word timings); VADER and spaCy optional |
| Reframe | YuNet face detector on onnxruntime (already a dependency); the 232 KB model is downloaded on first `face_track` use and SHA-256 checked ([ADR-005](docs/decisions/005-face-track-reframe.md)) |
| Captions | pysrt / webvtt-py |
| Subtitle images | Pillow + NumPy |
| UI | React 19 + Vite 8 + shadcn/ui |
| Tests | pytest + vitest |

**Performance & dependencies.** Each reel renders in a single ffmpeg pass from the source. MoviePy has been removed, and the binary is the one bundled by `imageio-ffmpeg`, never a system ffmpeg; yt-dlp merges video and audio with it too (`ffmpeg_location`), and URL previews run `python -m yt_dlp`, so neither `ffmpeg` nor `yt-dlp` needs to be on `PATH`. `av` is pinned at 18.1.0 because 19.x breaks faster-whisper 1.2.1. Measured numbers and the deliberate output changes are recorded in [ADR-004](docs/decisions/004-ffmpeg-render-pipeline.md); the concurrency model is described in [docs/architecture.md](docs/architecture.md).

## Environment Variables

See `.env.example` for the full list. Key settings:

| Variable | Default | Description |
|---|---|---|
| `YTVIDEO_JOB_STORE` | `sql` | `sql` or `memory` |
| `YTVIDEO_DB_URL` | `sqlite+aiosqlite:///./reelsmith.db` | Database URL (`.env.example` points it at the docker-compose Postgres) |
| `YTVIDEO_MAX_CONCURRENT_JOBS` | `1` | Pipelines running at once; extra jobs wait as `pending` |
| `YTVIDEO_MAX_PARALLEL_CHAPTERS` | `1` | Chapters processed concurrently within a job |
| `YTVIDEO_SEGMENT_PROVIDER` | `chapter` | `chapter`, `local_heuristic`, or `stub` |
| `YTVIDEO_REFRAME_PROVIDER` | `letterbox` | `letterbox` or `face_track` (the 9:16 window follows the speaker's face; falls back to `letterbox`) |
| `YTVIDEO_SERVE_FRONTEND` | `false` | Serve built React app from FastAPI |
| `YTVIDEO_REQUIRE_AUTH` | `false` | Enable API key auth |
| `YTVIDEO_API_KEY` | `null` | API key when auth enabled |
| `YTVIDEO_WHISPER_BEAM_SIZE` | `1` | Whisper beam size (bench: `scripts/bench_whisper.py`) |
| `YTVIDEO_WHISPER_VAD_FILTER` | `true` | Silero VAD before decoding; stops Whisper skipping speech after long silences |
| `YTVIDEO_WHISPER_CPU_THREADS` | `8` | CTranslate2 CPU threads; `0` = library default (4), use it on hosts with < 8 cores |
| `YTVIDEO_WHISPER_WARMUP` | `true` | Load the Whisper model in the background at API start-up |

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

The API is served at both `/x` and `/api/x` (`app/api_prefix.py`, [ADR-005](docs/decisions/005-api-route-prefix.md)). The UI calls `/api/...`: in dev the Vite proxy strips `/api`, and with `YTVIDEO_SERVE_FRONTEND=true` FastAPI strips it itself, so `curl localhost:8000/api/health` and `curl localhost:8000/health` both answer.
