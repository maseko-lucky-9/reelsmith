"""One-off copy of every row in the SQLite database into Postgres.

    python -m scripts.migrate_sqlite_to_postgres                 # ./reelsmith.db -> the compose Postgres
    python -m scripts.migrate_sqlite_to_postgres --source other.db --target postgresql+asyncpg://...

The source file is never modified: it is copied to ``data/backups/`` first and
the copy is migrated to the current schema and read. The target is migrated to
head, then filled table by table in foreign-key order inside one transaction.
Rows already present (same primary key, or a seeded row) are skipped, so a
re-run is safe. Exits non-zero if any source primary key is missing afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import batched
from pathlib import Path
from typing import Any

from sqlalchemy import Table, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import app.db.models  # noqa: F401  (registers every mapped table)
from app.db.base import Base
from app.db.migrate import upgrade_to_head

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO / "reelsmith.db"
DEFAULT_TARGET = "postgresql+asyncpg://reelsmith:reelsmith@127.0.0.1:5432/reelsmith"
BACKUP_DIR = REPO / "data" / "backups"
BATCH_SIZE = 500


@dataclass(frozen=True, slots=True)
class Report:
    source_counts: dict[str, int]
    inserted: dict[str, int]
    missing: dict[str, list[tuple[Any, ...]]] = field(default_factory=dict)


def _to_target_row(table: Table, row: Mapping[str, Any]) -> dict[str, Any]:
    """A source row ready to insert: SQLite drops time zones, and every
    timestamp this app wrote is UTC, so naive datetimes are stamped UTC
    (asyncpg would otherwise read them as this machine's local time)."""
    out: dict[str, Any] = {}
    for column in table.columns:
        value = row[column.key]
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        out[column.key] = value
    return out


def _engine(url: str) -> AsyncEngine:
    # hide_parameters: a failing INSERT would otherwise print the row's values,
    # and social_accounts / webhooks rows hold encrypted tokens and secrets.
    return create_async_engine(url, hide_parameters=True)


async def _count(conn: Any, table: Table) -> int:
    return (await conn.execute(select(func.count()).select_from(table))).scalar_one()


async def copy_all(src: AsyncEngine, dst: AsyncEngine) -> dict[str, int]:
    """Copy every table of ``src`` into ``dst`` (both already at head); return
    how many rows each table gained. One target transaction: all or nothing."""
    insert = pg_insert if dst.dialect.name == "postgresql" else sqlite_insert
    inserted: dict[str, int] = {}
    async with src.connect() as source, dst.begin() as target:
        for table in Base.metadata.sorted_tables:
            # ponytail: whole table in memory; this database holds hundreds of rows.
            rows = (await source.execute(select(table))).mappings().all()
            before = await _count(target, table)
            statement = insert(table).on_conflict_do_nothing()
            for chunk in batched(rows, BATCH_SIZE):
                await target.execute(statement, [_to_target_row(table, row) for row in chunk])
            inserted[table.name] = await _count(target, table) - before
    return inserted


async def _primary_keys(conn: Any, table: Table) -> set[tuple[Any, ...]]:
    pk = list(table.primary_key.columns)
    return {tuple(row) for row in (await conn.execute(select(*pk))).all()}


async def missing_keys(src: AsyncEngine, dst: AsyncEngine) -> dict[str, list[tuple[Any, ...]]]:
    """Primary keys present in ``src`` but absent from ``dst``, by table."""
    missing: dict[str, list[tuple[Any, ...]]] = {}
    async with src.connect() as source, dst.connect() as target:
        for table in Base.metadata.sorted_tables:
            lost = await _primary_keys(source, table) - await _primary_keys(target, table)
            if lost:
                missing[table.name] = sorted(lost)
    return missing


def _backup(source: Path, backup_dir: Path) -> Path:
    """A consistent copy of the live SQLite file (sqlite's own backup API)."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    copy = backup_dir / f"reelsmith-{datetime.now(UTC):%Y%m%dT%H%M%S}.db"
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as live, closing(sqlite3.connect(copy)) as dest:
        live.backup(dest)
    return copy


async def run(source: Path, target_url: str, *, backup_dir: Path = BACKUP_DIR) -> Report:
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")
    backup = await asyncio.to_thread(_backup, source, backup_dir)
    source_url = f"sqlite+aiosqlite:///{backup}"
    await asyncio.to_thread(upgrade_to_head, source_url)
    await asyncio.to_thread(upgrade_to_head, target_url)
    src, dst = _engine(source_url), _engine(target_url)
    try:
        async with src.connect() as conn:
            counts = {t.name: await _count(conn, t) for t in Base.metadata.sorted_tables}
        inserted = await copy_all(src, dst)
        return Report(counts, inserted, await missing_keys(src, dst))
    finally:
        await src.dispose()
        await dst.dispose()


def _print(report: Report) -> None:
    print(f"{'table':28} {'in source':>9} {'copied':>7}")
    for name, count in report.source_counts.items():
        print(f"{name:28} {count:>9} {report.inserted[name]:>7}")
    for name, keys in report.missing.items():
        print(f"MISSING in {name}: {keys}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", default=DEFAULT_TARGET, help="SQLAlchemy async URL")
    args = parser.parse_args(argv)
    report = asyncio.run(run(args.source, args.target))
    _print(report)
    if report.missing:
        print("copy incomplete: rows above did not arrive", file=sys.stderr)
        return 1
    print("ok: every source row is in the target")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
