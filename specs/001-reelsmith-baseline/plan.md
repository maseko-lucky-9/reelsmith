# Implementation Plan: Reelsmith Baseline

**Branch**: `n/a (baseline inventory)` | **Date**: 2026-10-09 | **Spec**: [spec.md](spec.md)

This plan describes the system as built. It does not duplicate the design documents; it links them. Work to close the gaps is in [tasks.md](tasks.md).

## Summary

FastAPI backend with an asyncio orchestrator that turns a video URL into captioned vertical clips, plus a React/Vite dashboard. Design is recorded in [docs/architecture.md](../../docs/architecture.md) and ADR-[001](../../docs/decisions/001-react-dashboard.md), [002](../../docs/decisions/002-opus-clip-ui-redesign.md), [003](../../docs/decisions/003-opusclip-feature-parity.md), [004](../../docs/decisions/004-ffmpeg-render-pipeline.md).

## Technical Context

**Language/Version**: Python 3.14 (CI; 3.12 unsupported), TypeScript 6.0.x
**Primary Dependencies**: FastAPI 0.143, SQLAlchemy 2.1.4 + Alembic 1.20, yt-dlp, faster-whisper, imageio-ffmpeg + PyAV, React 19, Vite 8, Tailwind 4, TanStack Router/Query (`requirements.txt`, `web/package.json`)
**Storage**: SQLite by default, PostgreSQL supported (`YTVIDEO_DB_URL`); media on the local filesystem ([docs/db-parity.md](../../docs/db-parity.md))
**Testing**: pytest + pytest-asyncio (markers `integration`, `live`, `e2e`, `playwright`); Vitest and Playwright for `web/`
**Target Platform**: developer Mac and Linux CI (`.github/workflows/ci.yml`)
**Project Type**: web application (API + SPA)
**Performance Goals**: see SC-001 in the spec (measured, not targeted)
**Constraints**: bundled ffmpeg only; offline default test run
**Scale/Scope**: 46 HTTP operations, 38 event types, 16 tables, 18 web routes

## Constitution Check

| Principle | Status | Enforcement today |
|---|---|---|
| I. One configuration surface | Exceptions E1, E2 | `tests/unit/test_settings_module.py` |
| II. Orchestrator sequences; events report | Exception E6 (CLAUDE.md wording) | `tests/unit/test_event_bus.py`, `test_orchestrator.py` |
| III. One ffmpeg | Exceptions E3, E4 | `tests/unit/test_no_moviepy_in_app.py`, `test_ffmpeg_tools.py` |
| IV. Offline, deterministic default tests | Holds | `pyproject.toml` `addopts`, `ci.yml` |
| V. Additive migrations, both engines | Holds (13 drift operations from `alembic check`, T018) | `docs/db-parity.md` |
| VI. Secrets | Exception E5 | `token_vault.py`; gitleaks not enforced |
| VII. React dashboard is the UI | Holds | `tests/unit/test_no_streamlit_in_app.py` |
| VIII. Decisions recorded | Holds | `docs/decisions/` |

## Project Structure

### Documentation (this feature)

```text
specs/001-reelsmith-baseline/
├── spec.md
├── plan.md
└── tasks.md
```

### Source Code (repository root)

```text
app/            routers/, services/, workers/orchestrator.py, bus/, domain/, db/, platforms and social adapters
alembic/        17 revisions
web/            React + Vite SPA (src/routes, Vitest, Playwright)
tests/          unit/, contract/, e2e/, integration/
docs/           architecture.md, decisions/, wave gates, db-parity.md, archive/
```

**Structure Decision**: unchanged web-application layout; this baseline records it rather than proposing one.

## Complexity Tracking

None. No change is proposed by this plan.
