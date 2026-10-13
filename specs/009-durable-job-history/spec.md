# Feature Specification: Durable job history

**Feature**: `009-durable-job-history` | **Date**: 2026-10-13 | **Decision**: [ADR-009](../../docs/decisions/009-postgres-default-store.md)

## Problem

After a restart the app forgot earlier extractions. The SQL job store existed but kept only the columns of `jobs` (no title, duration, folders, request options or chapter artifacts), the README recommended the in-memory store, the UI showed only the newest 20 jobs, downloads went to `/tmp`, and the retention janitor deleted clips after 30 days. The history lived in a SQLite file with no container, and the full stack took three manual commands.

## Requirements

- **FR-001**: The integration tests MUST NOT be able to reach the application's database. The `db_store` fixture's database MUST be named `*_test`, on a local host, created and migrated on first use.
- **FR-002**: `SqlJobStore` MUST persist and reload every `JobState` field except the derived ones (`output_paths`, from the clips; `chapters_done`, an atomic counter), including each chapter's artifacts. A restart MUST return a job as it was.
- **FR-003**: `update()` MUST delete the stored chapters a mutator removes, and MUST NOT write chapter artifacts (`upsert_chapter` does).
- **FR-004**: A row written before FR-002 (no `jobs.state` / `chapters.artifacts`), or whose stored JSON no longer fits the model, MUST still load. One unreadable field MUST NOT fail a job listing or cost the job its other fields. Listings (`list_jobs`, `find_job_by_url`) leave chapter artifacts out; `get` returns them.
- **FR-005**: The job list MUST ask for up to 500 jobs, not the API default of 20.
- **FR-006**: One command MUST copy every row of the SQLite database into Postgres without modifying the SQLite file, keep every timestamp exact in any local time zone, be safe to re-run, and exit non-zero if any source primary key is missing afterwards.
- **FR-007**: One command (`scripts/start.sh`) MUST start Docker if needed, the Postgres container (only the database is containerised), the API and the UI, refuse busy ports, require the API to report the SQL store, and on exit stop the API and UI but not the database.
- **FR-008**: The documented configuration (`.env.example`) MUST keep history: Postgres URL, downloads under `data/downloads`, retention 365 days. The code defaults are unchanged (SQLite, 30 days); `start.sh` supplies the Postgres URL itself.

## Out of scope

- Resuming a job interrupted by a restart (it is marked failed, with what it had).
- Recovering media already deleted from `/tmp` or by retention.
- Back-filling `title` / `duration` for jobs stored before FR-002.
- Containerising the API or the UI; a Windows launcher.

## Status

| Requirement | State | Proving test |
|---|---|---|
| FR-001 | Done | `tests/unit/test_db_safety.py` |
| FR-002 | Done | `tests/unit/test_job_store_state_roundtrip.py`, `tests/unit/test_restart_persistence.py` |
| FR-003 | Done | `test_update_that_prunes_chapters_deletes_them`, `tests/unit/test_orchestrator_reprompt.py` (both stores) |
| FR-004 | Done | `tests/unit/test_job_store_state_roundtrip.py` (legacy row, unreadable state, one bad field, bad `pipeline_options`, listing leaves chapters out), `tests/integration/test_sql_job_store_state.py` (Postgres) |
| FR-005 | Done | `web/src/api/client.test.ts` |
| FR-006 | Done | `tests/unit/test_migrate_sqlite_to_postgres.py`, `tests/integration/test_migrate_to_postgres.py` |
| FR-007 | Done | `tests/unit/test_start_script.py` |
| FR-008 | Done (`.env.example` and the local `.env`) | n/a, configuration |
