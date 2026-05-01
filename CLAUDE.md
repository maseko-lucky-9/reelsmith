# Reelsmith — CLAUDE.md

## Project

Learning project: YouTube-to-reels pipeline. FastAPI backend + Streamlit UI.

## Tech Stack

- **Python 3.11+** with `asyncio`
- **FastAPI** — HTTP API + SSE job progress
- **yt-dlp** — video download
- **MoviePy** — video editing/rendering
- **SpeechRecognition** — transcription (Google backend)
- **Streamlit** — UI thin client
- **pytest + pytest-asyncio** — tests

## Entry Points

| What | Command |
|---|---|
| API server | `uvicorn app.main:app --reload` |
| UI | `cd ui && streamlit run streamlit_app.py` |
| Tests | `pytest` |

## Key Conventions

- All env vars are prefixed `YTVIDEO_` and defined in `app/settings.py`.
- Domain events live in `app/domain/events.py`; all inter-service communication goes through `app/bus/event_bus.py`.
- Services are stateless functions — state lives in `JobStore` on `app.state`.
- `app/compat.py` must be imported before any MoviePy import (monkey-patches deprecated stdlib).
- Test markers: `integration` (real network), `live` (real YouTube), `e2e` (fixtures), `playwright` (UI). Default run excludes `integration`, `live`, `playwright`.

## Build & Test

```bash
# Install
pip install -r requirements.txt

# Run all fast tests
pytest

# Run with integration tests
pytest -m integration
```

Always run `pytest` after code changes before committing.
