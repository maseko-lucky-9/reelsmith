# Tasks: Durable job history

**Input**: [spec.md](spec.md), [ADR-009](../../docs/decisions/009-postgres-default-store.md)

- [x] T058 [FR-001] (done: `DEFAULT_TEST_DB_URL` is `reelsmith_test`; `resolve_test_db_url` refuses names not ending in `_test`; `db_store` creates the database and migrates it with `upgrade_to_head`) Give the integration tests their own database. Proving test: `tests/unit/test_db_safety.py`
- [x] T059 [FR-002..004] (done: revision `q5r6s7t8u9v0` adds `jobs.state` and `chapters.artifacts`; `SqlJobStore` stores and reloads them; `update()` deletes pruned chapters; `app.db.migrate.upgrade_to_head` runs migrations on an explicit URL) Persist every job field and chapter artifact. Proving test: `tests/unit/test_job_store_state_roundtrip.py`, `tests/unit/test_restart_persistence.py`
- [x] T060 [FR-005] (done: `listJobs()` defaults to `limit=500`) Show the whole job history. Proving test: `web/src/api/client.test.ts`
- [x] T061 [FR-006] (done: `scripts/migrate_sqlite_to_postgres.py`) Copy the SQLite history to Postgres (`hide_parameters` keeps encrypted tokens out of error output). Proving test: `tests/unit/test_migrate_sqlite_to_postgres.py`, `tests/integration/test_migrate_to_postgres.py`
- [x] T062 [FR-007] (done: `scripts/start.sh`; compose project `reelsmith`, port bound to `127.0.0.1`, `reelsmith_test` created on a fresh volume) One command to start the stack. Proving test: `tests/unit/test_start_script.py`
- [x] T063 [FR-008] (done: `.env.example` and the local `.env` default to Postgres, `data/downloads` and 365-day retention; README, CLAUDE.md, `docs/db-parity.md` updated) Make history-keeping the default and document it
