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

URLs are routed via a `PlatformAdapter` strategy registry (`app/services/platforms/`). Unsupported URLs are rejected at submission time with HTTP 400. MP4 uploads and, with `YTVIDEO_GENERATE_ENABLED=true`, text briefs run through the same pipeline.

## What It Does Today

**Default pipeline.** Every job gets the following by default (most stages have a per-job switch in `PipelineOptions`):

- one clip per chapter; a source without chapters is one "Full Video" clip;
- faster-whisper word timings and burned-in captions;
- a letterboxed 9:16 reel rendered in one ffmpeg pass;
- a thumbnail;
- social copy from a local Ollama model, when it answers;
- an export folder with `manifest.csv`;
- live per-stage progress over SSE.

Speech enhancement cleans only the audio used for transcription. AI hook text and filler removal are per-job options, off by default.

**Opt-in stages.** These are off in a default install:

| Feature | Turn on with | Default |
|---|---|---|
| Clip discovery in sources without chapters, and reprompt by prompt or length range ([ADR-007](docs/decisions/007-clip-discovery-and-reprompt.md)) | `YTVIDEO_SEGMENT_PROVIDER=local_heuristic` | `chapter`: one "Full Video" clip; a reprompt works only with an explicit time range |
| Re-rank of the discovered clips by a local LLM, blended with the heuristic score (ADR-007, *Re-rank*; needs clip discovery on) | `YTVIDEO_SEGMENT_RERANK_PROVIDER=ollama` (uses `YTVIDEO_OLLAMA_*`; Ollama down or a bad reply keeps the heuristic ranking) | `none`: the heuristic ranking only |
| Face-tracked 9:16 crop instead of the letterbox ([ADR-006](docs/decisions/006-face-track-reframe.md)) | `YTVIDEO_REFRAME_PROVIDER=face_track` | `letterbox` |
| B-roll inserts (up to two 3 s windows per reel) | `YTVIDEO_BROLL_PROVIDER=local` (keyword-named mp4s in `YTVIDEO_BROLL_LIBRARY_DIR`) or `pexels` (needs `YTVIDEO_PEXELS_API_KEY`) | `none` |

**Curation and output:**

- like and dislike;
- re-render one clip from the job's retained source;
- a timeline editor that saves a versioned timeline and returns a render plan (nothing renders a timeline into a video yet);
- Premiere and DaVinci XML;
- a bulk zip export;
- publish now. `YTVIDEO_SOCIAL_PROVIDER` defaults to `stub`; YouTube has a real OAuth adapter, and TikTok has a cookie session or an n8n sidecar.

**Removed.** Scheduled publishing was dropped (FR-032): `POST /social/publish` rejects `schedule_at` with 422.

**Not part of the render or the pipeline.** The code exists, but nothing in a job uses it:

- **Unwired services.** Animated captions, transitions, brand vocabulary, the profanity filter and voice-over. Brand templates are stored and a job can name one, but no render applies it; the UI marks these settings "Not applied to renders yet".
- **No routes.** Analytics, share links, webhooks and API tokens have tables and services but no routes of their own. Workspaces have tables only.

Both groups are roadmap specs in [`specs/README.md`](specs/README.md).

## Quick Start

```bash
# 1. Install Python deps (Python 3.14 required; 3.12 is unsupported, see CLAUDE.md)
python3.14 -m venv .venv-314 && source .venv-314/bin/activate
pip install -r requirements.txt

# 2. Settings: Postgres URL, media under data/downloads, 365-day retention (skips an existing .env)
cp -n .env.example .env

# 3. Start everything: Postgres in Docker, the API and the React UI
scripts/start.sh

# 4. Once, if you have jobs in the old SQLite file (./reelsmith.db): copy them to Postgres
python -m scripts.migrate_sqlite_to_postgres

# 5. Install the git hooks (once per clone)
pip install pre-commit && pre-commit install
```

`scripts/start.sh` starts Docker Desktop if it is stopped (macOS), brings up the `postgres` container (only the database runs in Docker), waits until it is healthy, then starts the API on <http://127.0.0.1:8000> and the UI on <http://127.0.0.1:5173>. Ctrl-C stops the API and the UI; the database keeps running, and so does your job history. Stop it with `docker compose stop postgres`. Data lives in the `reelsmith_postgres_data` Docker volume, so it survives restarts, `docker compose down` and reboots (not `down -v`).

`start.sh` always uses the Docker Postgres: it exports `YTVIDEO_DB_URL` (and `YTVIDEO_JOB_STORE=sql`), which beats a `YTVIDEO_DB_URL` in `.env`, unless you already set one in your shell. The API migrates the schema itself on every start, so a restart never needs a manual step. Reels and downloads go to `data/downloads`. The retention janitor retires clips and deletes their files (only below `data/downloads`) after `YTVIDEO_RETENTION_DAYS`: the code default is 30, and `.env.example` sets 365, so copy it (step 2) to keep your history.

Running the pieces by hand: `docker compose up -d --wait postgres`, then `uvicorn app.main:app --reload` and `cd web && pnpm install && pnpm dev`. With `YTVIDEO_JOB_STORE=memory` nothing is kept across restarts.

`pre-commit install` wires `.pre-commit-config.yaml` into `.git/hooks/pre-commit`, so every commit runs gitleaks on the staged changes (using `.gitleaks.toml`) plus basic hygiene checks (large files, merge markers, YAML, private keys). A detected secret blocks the commit. CI runs the same gitleaks scan in the `secrets` job.

Open **<http://127.0.0.1:5173>** in your browser.

Settings come from `YTVIDEO_*` environment variables or a `.env` file in the project root. `.env.example` lists them all; note that it points `YTVIDEO_DB_URL` at the docker-compose Postgres. To try the opt-in stages, set the variables listed under [What It Does Today](#what-it-does-today).

> **Note:** Only process videos you have the right to use. Check the platform's terms of service before downloading.

## Job History and Backups

**A restart keeps** every job with its status, title, duration, folders and request options (caption format, aspect ratio, language, prompt, pipeline options), each chapter's stored artifacts (status, paths, transcript) and every clip row (scores, likes, retired flag, hooks, B-roll, caption style). The home page lists up to 500 projects.

**A restart does not keep**
- A job that was running when the app stopped. It is marked `failed` ("interrupted by restart") with whatever it had finished; it is not resumed.
- Media that is gone from disk. Files live under `data/downloads` until the retention janitor removes them (`YTVIDEO_RETENTION_DAYS`).
- The title, duration and chapter details of jobs stored before [ADR-009](docs/decisions/009-postgres-default-store.md): those were never saved.
- Live progress history and the in-memory job queue. The job page refetches the job when it reconnects.

**Back up and restore.** The database lives in the `reelsmith_postgres_data` Docker volume. A dump covers every table, not the media files, so back up `data/downloads` separately.

```bash
# Back up (the stack can keep running). data/backups/ is gitignored: a dump holds encrypted OAuth tokens
mkdir -p data/backups
docker compose exec -T postgres pg_dump -U reelsmith reelsmith > data/backups/reelsmith.sql

# Restore into an empty database, here a scratch one
docker compose exec -T postgres psql -U reelsmith -d postgres -c "CREATE DATABASE restore_check"
docker compose exec -T postgres psql -U reelsmith -d restore_check < data/backups/reelsmith.sql
```

**Mapping the export folder.** With `YTVIDEO_EXPORT_BASE_FOLDER` set (a Syncthing share, say), every job also exports its clips and a `manifest.csv` to `<folder>/<job_id>/`. Runs on the old in-memory store left such folders with no database record, and the database's clip paths (`/tmp/yt/...`) point at files macOS has since cleared. `python -m scripts.map_inbox_to_db` repairs both; it is a dry run until you pass `--apply`:

```bash
python -m scripts.map_inbox_to_db            # report only: what would change
python -m scripts.map_inbox_to_db --apply    # write it (take a dump first, see above)
```

- A job the database knows: each clip whose recorded file is gone is re-pointed at its copy in the folder (an `.enhanced` or `.enhanced-audio` clip falls back to its pre-enhancement export). A path that still resolves is never replaced.
- A folder the database has never seen: imported as a completed job (`inbox://<job_id>`, titled `Inbox export <id>`, dated from the export) with a chapter and a clip per manifest row: title, description, hashtags and duration. Start is 0 and there is no score, transcript or thumbnail, and no source video, so re-render and reprompt answer 409 "source video not retained".
- It never writes to the folder, because Syncthing would carry the edit to your other devices. It is safe to run again, and it lists every row or file it skipped and every live clip it found no file for. It refuses an export folder that lies inside the download folder, where the janitor would own its files.
- The retention janitor never deletes below the export folder: it removes clip files only under the download path (`data/downloads`), so a clip that points at an export copy is retired when it expires but its file stays. For the same reason "Enhance speech" answers 409 for such a clip: it writes a new file beside the original, and that would sync to your other devices.
- An mp4 under 8 bytes is a test-run placeholder: the tool skips it and reports it.

**Repairing job metadata.** Jobs stored before [ADR-009](docs/decisions/009-postgres-default-store.md) have no title or duration, so the job page says "Processing…". Nothing needs re-running and nothing is downloaded: `python -m scripts.repair_job_metadata` asks yt-dlp for the title and duration of each such finished job's URL (single video, no download) and writes them onto the existing job. An `upload://` job gets its file name; a job imported from the export folder gets the title in its manifest when there is one. A title already set is never overwritten, a video that is gone or needs a login is listed and left alone, and it never writes to the export folder. It is a dry run until `--apply` (dump first); a second run only retries what failed. `--drop-placeholders` (also a dry run until `--apply`) removes imported jobs that hold nothing but placeholder files. Transcripts, scores and thumbnails of imported clips are not recoverable without the source videos.

To replace the live history, stop the API first, `DROP DATABASE reelsmith` and `CREATE DATABASE reelsmith` through the `postgres` database as above, then restore into `reelsmith`. That deletes the current history, so take a dump first. `docker compose down -v` or `docker volume rm reelsmith_postgres_data` also deletes it (plain `docker compose down` and reboots do not), and the next `scripts/start.sh` then starts a new, empty database. If the volume is lost, restore the latest dump as above, or re-run `python -m scripts.migrate_sqlite_to_postgres`: it copies from your old SQLite file, which the app never modifies, so it brings back every job stored before the move to Postgres (not later ones). `scripts/deploy.sh` tar-snapshots the volume to `data/backups/` before it changes anything.

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
app/bus/                 — async event bus + job store (memory / SQL: SQLite or Postgres)
app/db/                  — SQLAlchemy ORM models, alembic migrations, `migrate.py` (`upgrade_to_head(url)`)
scripts/start.sh         — one command: Postgres in Docker + API + UI
scripts/migrate_sqlite_to_postgres.py — one-off copy of the SQLite history into Postgres
scripts/map_inbox_to_db.py — map the export folder's clips and job folders into the database
scripts/repair_job_metadata.py — fill missing job titles and durations from the URL; drop placeholder imports
```

## Live Progress Timeline

The `/jobs/$jobId` page renders a per-stage timeline while the pipeline runs. Stages: prepare workspace → download source → detect chapters → extract clips → transcribe → caption → render → thumbnails+social → export & manifest → done. Per-chapter stages show `N/M` sub-progress.

- **Live updates, no polling:** the backend sends named SSE events, and `useJobSSE` registers a listener for every backend event type. Each event refreshes only that job and its clips. The job page has no `refetchInterval`, and the stream is open only while the job is `pending`/`running` or a reprompt it queued is running.
- **Data plane:** `useJobSSE` mirrors every SSE event into the React Query cache `['job-events', jobId]`. `deriveStageStates(jobState, events)` is a pure helper — `JobState` is the source of truth, events are a low-latency optimisation. Max-merge reconciliation between SSE counts and `JobState.chapters[i]` artifact fields means a stage never un-completes (kills SSE-reconnect drift and tab-refocus races in one rule).
- **Accessibility:** single visually-hidden `role="status" aria-live="polite"` region announces only stage transitions (~10/job, not ~60). Active row gets `aria-current="step"` plus a static emerald left-border so reduced-motion users still get a non-animation cue.
- **Resilience:** `<TimelineErrorBoundary>` wraps the component; a malformed `JobState` falls back without blanking the page.

## Stack

| Layer | Library |
|---|---|
| API | FastAPI + Uvicorn |
| Database | Postgres 16 in Docker (what `scripts/start.sh` runs) or SQLite for a Docker-free run, through SQLAlchemy 2 async + Alembic |
| Video download | yt-dlp (YouTube / Facebook / TikTok / Instagram via PlatformAdapter registry) |
| Video editing | ffmpeg (bundled via imageio-ffmpeg) + PyAV |
| Transcription | faster-whisper (word-level) |
| Segment scoring | NumPy + standard library (wav RMS, word timings); VADER and spaCy optional |
| Reframe | YuNet face detector on onnxruntime (already a dependency); the 232 KB model is downloaded on first `face_track` use and SHA-256 checked ([ADR-006](docs/decisions/006-face-track-reframe.md)) |
| B-roll | Local keyword-named mp4 library, or the Pexels video API over httpx (cached, `*.pexels.com` downloads only) |
| Captions | pysrt / webvtt-py |
| Subtitle images | Pillow + NumPy |
| UI | React 19 + Vite 8 + shadcn/ui |
| Tests | pytest + vitest |

**Performance & dependencies.** Each reel renders in a single ffmpeg pass from the source. MoviePy has been removed, and the binary is the one bundled by `imageio-ffmpeg`, never a system ffmpeg; yt-dlp merges video and audio with it too (`ffmpeg_location`), and URL previews run `python -m yt_dlp`, so neither `ffmpeg` nor `yt-dlp` needs to be on `PATH`. `av` is pinned at 18.1.0 because 19.x breaks faster-whisper 1.2.1. Measured numbers and the deliberate output changes are recorded in [ADR-004](docs/decisions/004-ffmpeg-render-pipeline.md); the concurrency model is described in [docs/architecture.md](docs/architecture.md).

## Environment Variables

See `.env.example` for the full list. Key settings:

| Variable | Default | Description |
|---|---|---|
| `YTVIDEO_JOB_STORE` | `sql` | `sql` or `memory` (`memory` keeps nothing across restarts) |
| `YTVIDEO_DB_URL` | `sqlite+aiosqlite:///./reelsmith.db` | Database URL. `.env.example` points it at the docker-compose Postgres, and `scripts/start.sh` exports that URL itself unless your shell already sets one |
| `YTVIDEO_EXPORT_BASE_FOLDER` | empty | Each job also exports its clips and a `manifest.csv` to `<folder>/<job_id>/`; empty means `<download_path>/<video>/exports`. Never swept by the janitor; `scripts/map_inbox_to_db.py` maps it into the database |
| `YTVIDEO_RETENTION_DAYS` | `30` | The janitor retires clips older than this and deletes their files below the download folder. `.env.example` sets `365` |
| `YTVIDEO_TEST_DB_URL` | `postgresql+asyncpg://reelsmith:reelsmith@localhost:5432/reelsmith_test` | Tests only: the database `pytest -m integration` TRUNCATEs. Must be local and named `*_test`; never your app database |
| `YTVIDEO_MAX_CONCURRENT_JOBS` | `1` | Pipelines running at once; extra jobs wait as `pending` |
| `YTVIDEO_MAX_PARALLEL_CHAPTERS` | `1` | Chapters processed concurrently within a job |
| `YTVIDEO_DEFAULT_DOWNLOAD_PATH` | `<project>/data/downloads` | Where each job's source video (kept for re-render and reprompt), its output folder and uploads (`uploads/`) live; gitignored. Do not point it at `/tmp`: the OS clears it, the sources are lost and re-render and reprompt answer 409 "source video not retained" |
| `YTVIDEO_SEGMENT_PROVIDER` | `chapter` | `chapter` (a source without chapters is one "Full Video" clip), `local_heuristic` (discover clips in it, and reprompt by prompt; ADR-007), or `stub` (tests) |
| `YTVIDEO_REFRAME_PROVIDER` | `letterbox` | `letterbox` or `face_track` (the 9:16 window follows the speaker's face; falls back to `letterbox`) |
| `YTVIDEO_BROLL_PROVIDER` | `none` | `none`, `local` (`YTVIDEO_BROLL_LIBRARY_DIR`, default `<project>/data/broll`) or `pexels` (`YTVIDEO_PEXELS_API_KEY`) |
| `YTVIDEO_SERVE_FRONTEND` | `false` | Serve built React app from FastAPI |
| `YTVIDEO_REQUIRE_AUTH` | `false` | Enable API key auth; also switches `/docs`, `/redoc` and `/openapi.json` off |
| `YTVIDEO_API_KEY` | `null` | API key when auth enabled |
| `YTVIDEO_WHISPER_BEAM_SIZE` | `1` | Whisper beam size (bench: `scripts/bench_whisper.py`) |
| `YTVIDEO_WHISPER_VAD_FILTER` | `true` | Silero VAD before decoding; stops Whisper skipping speech after long silences |
| `YTVIDEO_WHISPER_CPU_THREADS` | `8` | CTranslate2 CPU threads; `0` = library default (4), use it on hosts with < 8 cores |
| `YTVIDEO_WHISPER_WARMUP` | `true` | Load the Whisper model in the background at API start-up |

## Testing

```bash
# Unit + contract tests (no network, no Postgres)
pytest

# Integration tests (requires `docker compose up -d --wait postgres`). They use their own
# database, reelsmith_test (created on first use), and never touch your job history.
pytest -m integration

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

The API is served at both `/x` and `/api/x` (`app/api_prefix.py`, [ADR-005](docs/decisions/005-api-route-prefix.md)). The UI calls `/api/...`: in dev the Vite proxy strips `/api` and forwards to `http://127.0.0.1:8000` (set `YTVIDEO_API_URL`, for example `YTVIDEO_API_URL=http://127.0.0.1:8001 pnpm dev`, when another service holds that port), and with `YTVIDEO_SERVE_FRONTEND=true` FastAPI strips it itself, so `curl localhost:8000/api/health` and `curl localhost:8000/health` both answer.

The live OpenAPI schema is served by the backend at `/openapi.json` (and `/api/openapi.json`), with Swagger UI at `/docs`, while `YTVIDEO_REQUIRE_AUTH` is off. With auth on, these docs routes are switched off and answer 404 (FR-060, T043). The web client (`web/src/api/client.ts`) is hand-written, so there is no generated schema or types file to keep in sync (T038).

Reloading a client route such as `/uploads/new` or `/jobs/<id>` serves the UI (`app/spa_fallback.py`, T036): a browser navigation (`Accept: text/html`) to a route of `web/src/routeTree.ts` gets `index.html`, while `fetch`, `curl` and anything under `/api/...` get the API. Add a new client route to `CLIENT_ROUTES` in `app/spa_fallback.py` too; a test fails until you do.

## Decision Records

| ADR | Decision |
|---|---|
| [001](docs/decisions/001-react-dashboard.md) | Replace Streamlit with a React dashboard |
| [002](docs/decisions/002-opus-clip-ui-redesign.md) | Opus Clip UI redesign |
| [003](docs/decisions/003-opusclip-feature-parity.md) | OpusClip feature parity, Waves 1-3 (the MoviePy part of decision 2 is superseded by ADR-004) |
| [004](docs/decisions/004-ffmpeg-render-pipeline.md) | One-pass ffmpeg render pipeline, MoviePy removed |
| [005](docs/decisions/005-api-route-prefix.md) | Serve the API at both `/x` and `/api/x` |
| [006](docs/decisions/006-face-track-reframe.md) | Face-tracked reframe with YuNet on onnxruntime |
| [007](docs/decisions/007-clip-discovery-and-reprompt.md) | Clip discovery in chapterless sources, and reprompt |
| [008](docs/decisions/008-no-gpu-encode.md) | No GPU encode on Apple Silicon; cap the reel canvas and prefer H.264 |
| [009](docs/decisions/009-postgres-default-store.md) | Postgres in Docker is the default job store; one launcher starts the stack |
| [010](docs/decisions/010-map-export-folder.md) | Map the export folder into the database; the expiry sweep deletes only below managed roots |
| [011](docs/decisions/011-repair-job-metadata.md) | Repair job metadata from the URL instead of re-running the job; drop the placeholder imports |
