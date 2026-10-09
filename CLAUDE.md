# Reelsmith — CLAUDE.md

## Project

Learning project: YouTube-to-reels pipeline. FastAPI backend + React UI (`web/`); the Streamlit UI is legacy (`ui/_legacy/`).

## Tech Stack

- **Python 3.14** (CI; 3.12 is not supported: 4 orchestrator concurrency tests fail there, see PR #25), with `asyncio`
- **FastAPI** — HTTP API + SSE job progress
- **yt-dlp** — video download
- **ffmpeg** (bundled `imageio-ffmpeg` binary) + **PyAV** — one-pass rendering, probing, frame grabs (`app/services/ffmpeg_tools.py`)
- **faster-whisper** — transcription (word-level timings)
- **React + Vite** — UI (`web/`); Streamlit thin client is legacy (`ui/_legacy/`)
- **pytest + pytest-asyncio** — tests

## Entry Points

| What | Command |
|---|---|
| API server | `uvicorn app.main:app --reload` |
| UI | `cd web && pnpm dev` (legacy: `streamlit run ui/_legacy/streamlit_app.py`) |
| Tests | `pytest` |

## Key Conventions

- All env vars are prefixed `YTVIDEO_` and defined in `app/settings.py`.
- Domain events live in `app/domain/events.py`; job lifecycle and progress are reported through `app/bus/event_bus.py`. The orchestrator sequences stages; services are plain functions called directly.
- Services are stateless functions — state lives in `JobStore` on `app.state`.
- Run ffmpeg only through `app/services/ffmpeg_tools.run` (bundled binary, killed on timeout/cancel); never a system ffmpeg/ffprobe. MoviePy is not a dependency.
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

## Spec-Driven Development

- Constitution: `.specify/memory/constitution.md`. Baseline spec: `specs/001-reelsmith-baseline/spec.md` (status-tagged inventory).
- New work: `/speckit-specify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement`, one `specs/NNN-<slug>/` per feature.
- Open tasks live in each `specs/*/tasks.md`; `tasks/todo.md` indexes them and holds the finished parity history.
