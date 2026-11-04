# ADR-009: Postgres in Docker Is the Default Job Store; One Launcher Starts the Stack

**Status:** Accepted
**Date:** 2026-10-13
**Author:** Thulani Maseko
**Implements:** T058-T063 (spec 009)

## Context

After every restart the app seemed to forget earlier extractions. The SQL job store already existed, so the cause was not a missing database. We checked what a restart actually lost:

| Leak | Evidence |
|---|---|
| The README quick start ran `YTVIDEO_JOB_STORE=memory`. | `README.md` quick start, step 3. |
| `SqlJobStore` kept only the columns of `jobs`. `title`, `duration`, `current_step`, both folders, the request's `caption_format` and `target_aspect_ratio` and every chapter's artifacts were dropped, and `download_path` was hard-coded to `/tmp/yt`. | `SqlJobStore.create`/`update`, `upsert_chapter` (rebuilt a blank `ChapterArtifacts` on each call), `_record_to_state`. |
| The home page and the badge asked for the API default of 20 jobs. | `api.listJobs()`; 3 of 23 jobs were hidden. |
| Downloads went to `/tmp/yt`, which macOS clears. | 8 of 9 source videos were already gone. |
| The retention janitor retired clips and deleted their files after 30 days. | `YTVIDEO_RETENTION_DAYS=30`; 40 clips were already retired. |

The local database was a SQLite file, `./reelsmith.db`, relative to the working directory.

## Decision

1. **Postgres 16 in Docker (the `postgres` service of `docker-compose.yml`) is the default store; only the database is containerised.** The compose project is named `reelsmith` so the volume is `reelsmith_postgres_data` from any checkout, and the port is published on `127.0.0.1` only.
2. **`SqlJobStore` persists everything a job knows.** Two nullable JSON columns, added by revision `q5r6s7t8u9v0`:
   - `jobs.state` holds the `JobState` fields that have no column. The set is derived from the model, so a field added later persists without a migration.
   - `chapters.artifacts` holds one chapter's `ChapterArtifacts`. `update()` never writes chapters back (a stale snapshot could undo a concurrent chapter write); it only deletes the rows of chapters the mutator removed, which is how reprompt drops replaced chapters.
   - Rows written before this change load with defaults. An unreadable stored field (or `pipeline_options`) is dropped on its own, with a warning, instead of failing the listing or costing the job its other fields. Listings leave chapter artifacts out (the home page and the 30-second badge poll never read them, and transcripts dominate the payload); `get` returns them.
   - Rows copied from SQLite hold JSON `null` rather than SQL NULL in these columns (SQLAlchemy's JSON type writes `None` as JSON null). Every reader checks the type, so none breaks; a future `IS NULL` backfill must also match `'null'`.
3. **`app.db.migrate.upgrade_to_head(url)` is the one way to migrate a database.** It takes an explicit URL (`alembic/env.py` reads `config.attributes["db_url"]` first), uses no ini file so logging is not reset, and serves start-up, the copy script and the test fixture.
4. **A one-off script, `scripts/migrate_sqlite_to_postgres.py`, copies every row.** It migrates a backup of the SQLite file, never the original; copies in foreign-key order in one transaction; skips rows already present; stamps naive datetimes UTC (asyncpg would read them as local time); and exits non-zero if any source primary key is missing afterwards.
5. **`scripts/start.sh` starts the stack** (Docker Desktop if needed, the database, the API, the UI) and leaves the database running on exit. It exports `YTVIDEO_DB_URL`, which beats the one in `.env`, so the stack always uses the Docker database unless the shell already sets one.
6. **The integration tests get their own database.** `db_store` TRUNCATEs the job tables and used to default to the app's database, so `pytest -m integration` would have wiped the history this work keeps. It now defaults to `reelsmith_test`, creates and migrates it on first use, and `tests/db_safety.py` refuses any database whose name does not end in `_test`.
7. **Config:** `.env.example` and the local `.env` point at the Postgres URL, drop the `/tmp/yt` download override (the setting defaults to `data/downloads`) and keep jobs for 365 days. The code defaults are unchanged (SQLite, 30 days), so a checkout without a `.env` still runs the janitor on the 30-day window: the README says to copy `.env.example`.

## Alternatives rejected

- **Typed columns for every `JobState` field.** One migration per UI-only field; the JSON column plus a lenient reader is smaller and cannot break a listing.
- **Run the API in Docker too.** Not asked for, and it would put the bundled ffmpeg, the Whisper model cache and the download folder behind volume mounts.
- **Resume interrupted jobs.** The queue and the pipelines live in the process and the request payload is not stored. They are still marked failed at start-up, as before, and keep what they had.
- **Disable retention.** Chosen instead: a 365-day window, configuration only.

## Consequences

- A restart keeps every job, its fields, its chapters and its clips' rows. Media files already deleted from `/tmp` or by retention cannot be recovered; older jobs show their records without the files.
- Jobs created before this change have no `title`, `duration` or chapter details (they were never stored).
- The home page lists up to 500 jobs. Paging is a follow-up when the history outgrows that.
- Docker Desktop must be running to use the default store. `YTVIDEO_DB_URL=sqlite+aiosqlite:///./reelsmith.db` still works for a quick run without Docker.
- `update()` and `upsert_chapter()` are read-modify-write without row locks, as `update()` already was. Known ceiling: two writers on the same chapter lose one write, a prune that commits between an upsert's read and flush raises `StaleDataError`, and two first inserts of one chapter could leave duplicate rows. It needs `YTVIDEO_MAX_CONCURRENT_JOBS` above 1 (default 1) or a rerender queued during a reprompt. Upgrade path: `with_for_update()` on the selects and a unique `(job_id, chapter_index)`.
- Stopping the launcher stops the API's ffmpeg children before uvicorn, so an in-flight job ends as failed (it would be marked interrupted at the next start anyway).
