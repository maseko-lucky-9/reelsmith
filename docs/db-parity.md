# SQLite vs Postgres Parity Audit

**PR-0d** · ADR-003 §pre-flight · 2026-05-10

This document records the current parity contract between SQLite (dev) and
Postgres (production / CI integration tier). The Wave 1+ migration template
honours these rules; deviations from this audit must be flagged in the PR.

---

## Discriminators

| Knob | Default | Override | Where |
|---|---|---|---|
| `YTVIDEO_DB_URL` | `sqlite+aiosqlite:///./reelsmith.db` | Any SQLAlchemy async URL | [app/settings.py:38](../app/settings.py) |
| `YTVIDEO_JOB_STORE` | `sql` | `sql` \| `memory` | [app/settings.py:40](../app/settings.py) |

`YTVIDEO_JOB_STORE` selects the persistence layer (SqlJobStore vs in-memory).
The SQL flavour is determined entirely by `YTVIDEO_DB_URL`. There is no
`postgres` token; we recognise dialects from the URL scheme.

## Test tier mapping

| Test class | DB | Source |
|---|---|---|
| Unit (`tests/unit/`) | None / pure functions / monkeypatched | n/a |
| Contract (`tests/contract/`) | SQLite `:memory:` via dependency override | [tests/contract/test_brand_templates_router.py:19](../tests/contract/test_brand_templates_router.py) |
| Integration (`tests/integration/`) | Postgres 16 (docker-compose) | [tests/conftest.py:9](../tests/conftest.py) |
| E2E (`tests/e2e/`) | Postgres via real app stack | per-test |
| Web Playwright (`web/tests/e2e/`) | n/a (frontend smoke) | n/a |

The `db_store` fixture in [tests/conftest.py](../tests/conftest.py) hard-pins
`postgresql+asyncpg://reelsmith:reelsmith@localhost:5432/reelsmith`. Run
integration tests after `docker compose up -d postgres`.

## Migration rules (additive-only)

All Alembic migrations under [alembic/versions/](../alembic/versions/) follow
these rules. The pattern is established — Wave 1+ migrations must extend, not
break it.

1. **`op.batch_alter_table` for ALL ALTER ops** so SQLite (no native ALTER)
   stays in sync. Reference: [b2c3d4e5f6g7_add_pipeline_options.py:21-22](../alembic/versions/b2c3d4e5f6g7_add_pipeline_options.py).
2. **No `DROP COLUMN`** in Wave 1-3. New columns are nullable with a default.
3. **No `ALTER COLUMN TYPE` on existing rows.** Add a new column, backfill in
   a separate migration, deprecate the old column post-soak (out of scope for
   the parity programme).
4. **No `CREATE INDEX CONCURRENTLY`** — Postgres-only and not safe inside a
   migration transaction. Use plain `CREATE INDEX`; SQLite ignores
   concurrency.
5. **Foreign keys named explicitly** so SQLite's batch mode can rebuild them.
6. **JSON columns**: use `sa.JSON` (renders to JSON in SQLite ≥3.45 and JSONB
   in Postgres via type adapter). Avoid `JSONB`-specific operators in
   application code.
7. **`server_default`** for new not-null columns OR backfill in a follow-up
   migration before flipping nullability.

## Postgres-only features (W3)

The Wave 3 scheduler relies on `SELECT … FOR UPDATE SKIP LOCKED`, which has
no SQLite equivalent. Constraints:

| W3 Feature | SQLite | Postgres |
|---|---|---|
| `scheduled_posts` worker | **Unsupported** (worker refuses to start; logs `requires_postgres`) | Supported |
| `analytics` snapshots refresh | Supported (single-writer) | Supported |
| Webhook dispatcher retry budget | Supported | Supported |
| API token bcrypt store | Supported | Supported |

W3 PRs must:

- Detect dialect at startup and gate the worker with a clear log line.
- Document the SQLite limitation in the W3 release note.
- Keep the calendar / analytics UI functional read-only when the worker is
  disabled.

## Audit findings (current HEAD `5865534`)

| Item | Status |
|---|---|
| All migrations use `batch_alter_table` for ALTER ops | ✅ |
| No `DROP COLUMN` in any migration | ✅ |
| No `JSONB`-specific operators in `app/` | ✅ |
| No `CREATE INDEX CONCURRENTLY` | ✅ |
| Integration tests run against Postgres | ✅ |
| Contract tests run against SQLite `:memory:` | ✅ |
| `db_url` default is SQLite | ✅ |
| Dialect-specific code uses `bind.dialect.name` | n/a (none yet) |

No changes required for parity at this snapshot. This audit becomes the
acceptance gate for Wave 1+ migrations.

## How to flip the test suite to Postgres locally

```bash
docker compose up -d postgres
pytest -m integration -q
# Or, force the unit/contract tier through Postgres:
YTVIDEO_DB_URL=postgresql+asyncpg://reelsmith:reelsmith@localhost:5432/reelsmith \
  pytest tests/contract -q
```

Note: contract tests intentionally use SQLite `:memory:` for speed; running
them against Postgres is a sanity check only and not part of CI.

## How to verify a new Wave 1+ migration

```bash
# From a clean state — run on both engines.
rm -f reelsmith.db
alembic upgrade head                         # SQLite (default)
psql -h localhost -U reelsmith -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
YTVIDEO_DB_URL=postgresql+asyncpg://reelsmith:reelsmith@localhost:5432/reelsmith \
  alembic upgrade head                       # Postgres

# Schema dump diff (informational, not gating):
sqlite3 reelsmith.db .schema | sort > /tmp/sqlite.schema
pg_dump -s -h localhost -U reelsmith reelsmith | grep -E '^(CREATE|ALTER)' | sort > /tmp/pg.schema
diff /tmp/sqlite.schema /tmp/pg.schema | head -40
```

Differences in type names (`INTEGER` vs `BIGINT`, `TEXT` vs `VARCHAR`) are
expected. Differences in column counts, NOT NULL flags, or default values
are NOT and must be reconciled.
