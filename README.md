# Reelsmith

**Problem:** Manually trimming YouTube videos into captioned short-form clips is tedious and time-consuming for a solo creator.

Reelsmith is a learning project that automates the pipeline: download a YouTube video, transcribe it, generate SRT captions, burn subtitles into chapter clips, and produce 9:16 vertical reels — all through a FastAPI backend with a Streamlit UI.

## Architecture

```
ui/streamlit_app.py      — thin Streamlit client
app/main.py              — FastAPI app with SSE progress streaming
app/routers/             — HTTP endpoints (jobs, downloads, transcriptions, captions, renders)
app/services/            — domain logic per pipeline stage
app/workers/orchestrator — async job runner
app/domain/              — events, models, IDs
app/bus/                 — in-process async event bus + job store
```

## Stack

| Layer | Library |
|---|---|
| API | FastAPI + Uvicorn |
| Video download | yt-dlp |
| Video editing | MoviePy |
| Transcription | SpeechRecognition (Google) |
| Captions | pysrt / webvtt-py |
| Subtitle images | Pillow + NumPy |
| UI | Streamlit |
| Tests | pytest + pytest-asyncio + Playwright |

## Setup

```bash
python -m venv .venv-mac
source .venv-mac/bin/activate
pip install -r requirements.txt
```

## Running

```bash
# API (from repo root)
uvicorn app.main:app --reload

# UI (separate terminal)
cd ui
streamlit run streamlit_app.py
```

Environment variables (all prefixed `YTVIDEO_`):

| Variable | Default | Description |
|---|---|---|
| `YTVIDEO_DEFAULT_DOWNLOAD_PATH` | `/tmp/yt` | Where videos are saved |
| `YTVIDEO_MAX_THREAD_WORKERS` | `4` | Thread pool cap for CPU-heavy tasks |
| `YTVIDEO_TRANSCRIPTION_PROVIDER` | `google` | `google` or `stub` |
| `YTVIDEO_FONT_PATH` | auto-detected | Path to a `.ttf`/`.ttc` font |

## Testing

```bash
# Unit + e2e (default — no network)
pytest

# Integration (hits real network)
pytest -m integration

# Playwright UI smoke tests (requires running services)
pytest -m playwright
```
