# Reelsmith

**Problem:** Manually trimming YouTube videos into captioned short-form clips is tedious and time-consuming for a solo creator.

Reelsmith automates the pipeline: download a video, transcribe it with word-level timing, score clips by virality heuristics, burn karaoke subtitles, and produce 9:16 vertical reels — all through a FastAPI backend and a React dashboard.

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
app/main.py              — FastAPI app, job queue, SSE streaming
app/routers/             — jobs, clips, media, uploads, brand_templates
app/services/            — download, transcription, caption, render, segment_proposer,
                           reframe, broll, thumbnail
app/workers/orchestrator — async pipeline runner
app/domain/              — events, models, IDs
app/bus/                 — async event bus + job store (memory / Postgres)
app/db/                  — SQLAlchemy ORM models + alembic migrations
```

## Stack

| Layer | Library |
|---|---|
| API | FastAPI + Uvicorn |
| Database | Postgres 16 + SQLAlchemy 2 async + Alembic |
| Video download | yt-dlp |
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
