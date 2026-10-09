# Reelsmith Constitution

> **Status: RATIFIED 2026-10-09 by the owner ("ship it" after review).** Every principle below is distilled from the repository (`CLAUDE.md`, ADR-001…004, `docs/db-parity.md`, `ci.yml`, tests) and cites its source. Where the code does not yet honour a principle, the gap is listed under *Known exceptions*, not hidden. Do not add a principle that has no source.

## Core Principles

### I. One configuration surface
Runtime configuration is read through `app/settings.py`. Every variable is prefixed `YTVIDEO_`. New code reads `settings.<field>`, never `os.environ`.
Source: `CLAUDE.md` (Key Conventions), `docs/db-parity.md` (Discriminators).

### II. The orchestrator sequences; events report; services are stateless
`app/workers/orchestrator.py` is the only place stages are ordered. Job lifecycle and progress are domain events defined in `app/domain/events.py` and carried by `app/bus/event_bus.py` (the orchestrator subscribes to `VIDEO_REQUESTED`; the SSE route subscribes per job). Services are stateless functions; job and clip state lives in `JobStore` on `app.state`. Low-level helpers such as `ffmpeg_tools` may be imported directly.
Source: `CLAUDE.md` (Key Conventions), `docs/architecture.md`, `app/workers/orchestrator.py:71`, `app/routers/jobs.py:213`.

### III. One ffmpeg
All media rendering goes through `app/services/ffmpeg_tools.run`, which resolves `argv[0] == "ffmpeg"` to the bundled `imageio-ffmpeg` binary and kills the child on timeout or cancel. Probing and frame grabs use PyAV. MoviePy is not a dependency; a guard test enforces it.
Source: `CLAUDE.md`, ADR-004, `app/services/ffmpeg_tools.py:80,111`, `tests/unit/test_no_moviepy_in_app.py`.

### IV. The default test run is offline and deterministic
`pytest` excludes `integration`, `live` and `playwright` markers (`pyproject.toml` `addopts`). Tests that need the network or a real browser carry a marker. CI runs Python 3.14 only; 3.12 is unsupported. Frontend changes pass `pnpm test` and `pnpm build`.
Source: `CLAUDE.md` (Build & Test), `pyproject.toml`, `.github/workflows/ci.yml`, PR #25.

### V. Migrations are additive and run on both engines
SQLite (default) and PostgreSQL must both work. Alembic migrations use `op.batch_alter_table`, never drop columns or change column types on existing rows, name foreign keys explicitly, use `sa.JSON`, and give new not-null columns a `server_default`.
Source: `docs/db-parity.md` §Migration rules.

### VI. Secrets never live in source or at rest in plaintext
Social tokens are Fernet-encrypted (`app/services/token_vault.py`). `.env` is gitignored. A secrets scan runs before commit.
Source: ADR-003 decision 4, `.gitignore`, `.pre-commit-config.yaml` (gitleaks), `.gitleaks.toml`.

### VII. The React dashboard is the product UI
The UI is `web/` (React + Vite + TanStack). Streamlit is legacy under `ui/_legacy/` and may not be imported by `app/`.
Source: ADR-001, `CLAUDE.md`, `tests/unit/test_no_streamlit_in_app.py`.

### VIII. Decisions are recorded
A non-trivial architectural decision gets an ADR in `docs/decisions/NNN-<slug>.md` stating context, decision and consequences. Features are specified under `specs/NNN-<slug>/` with the Spec Kit workflow.
Source: `docs/decisions/001–004`, `CLAUDE.md`.

## Technical Constraints

- Backend: Python 3.14, FastAPI, SQLAlchemy 2.1 + Alembic, asyncio. Transcription: faster-whisper. Download: yt-dlp.
- Frontend: React 19, Vite 8, TypeScript (held at 6.0.x, see tasks), Tailwind v4, TanStack Router/Query, Vitest.
- Test markers: `integration` (real network), `live` (real YouTube), `e2e` (fixtures), `playwright` (UI).

## Development Workflow

- Run `pytest` after every backend change and `cd web && pnpm test && pnpm build` after every frontend change, before committing.
- Work on a feature branch; open a PR; merge on green CI (`ci.yml`: backend `pytest`, `pytest -m integration`, frontend test + build).
- A spec's status tags (`Implemented | Partial | Scaffolded-unwired | Missing`) are claims. `Implemented` requires a test that fails when the behaviour is broken.

## Known exceptions (the code does not yet honour these)

| # | Principle | Where | Detail | Tracked as |
|---|---|---|---|---|
| E1 | I | `app/main.py:73` | `SKIP_ALEMBIC` is unprefixed | tasks T001 |
| E2 | I | `app/logging_config.py:18`, `app/services/voiceover_service.py:137`, `token_vault.py:29`, `share_link_service.py:51`, `social/registry.py:22,25` | Prefixed variables read from `os.environ` instead of `settings`; `YTVIDEO_LOG_LEVEL`, `YTVIDEO_PIPER_MODEL`, `YTVIDEO_SHARE_LINK_SECRET` and `YTVIDEO_SOCIAL_PROVIDER[_<PLATFORM>]` are not declared in `Settings` at all | tasks T002 |
| E3 | III | `app/services/platforms/_yt_dlp_base.py:17` | Format `bestvideo+bestaudio` makes yt-dlp merge with whichever `ffmpeg` is on `PATH`; no `ffmpeg_location` is set | tasks T003 |
| E4 | III | `app/routers/jobs.py:59,87` | Shells out to the `yt-dlp` CLI on `PATH` | tasks T003 |
| E5 | VI | `.pre-commit-config.yaml`, `.github/workflows/ci.yml` | Gitleaks is configured but no hook is installed in `.git/hooks` and CI has no gitleaks step | tasks T004 |
| E6 | II | `CLAUDE.md` | Says "all inter-service communication goes through the event bus"; services import each other directly. The principle above is the accurate statement | tasks T005 |

## Governance

This constitution supersedes other practice notes for this repository. An amendment is a dated line in the version history with its reason; silent drift is a violation. `/speckit-analyze` treats a conflict with a MUST principle as CRITICAL, so unfixed gaps belong in *Known exceptions*, not in silence. Complexity beyond this document needs an ADR.

**Version**: 1.0.0 | **Ratified**: 2026-10-09 | **Last Amended**: 2026-10-09
