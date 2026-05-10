"""Postgres-backed scheduler (W3.2).

Replaces the W1 APScheduler scaffold once Wave 3 ships. Uses
``SELECT … FOR UPDATE SKIP LOCKED`` so multiple workers can lease
distinct rows without coordination — Postgres-only.

The pure-function ``claim_due_posts`` accepts a session bound to
either dialect; on SQLite it falls back to a non-locking SELECT
(documented in docs/db-parity.md as "scheduling unsupported on
SQLite — use Postgres").
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScheduledPost

log = logging.getLogger(__name__)


def _is_postgres(session: AsyncSession) -> bool:
    bind = session.get_bind()
    return getattr(bind, "dialect", None) is not None and bind.dialect.name == "postgresql"


async def claim_due_posts(
    session: AsyncSession,
    *,
    worker_id: str,
    now: datetime | None = None,
    limit: int = 10,
) -> list[ScheduledPost]:
    """Atomically claim up to ``limit`` due posts for this worker.

    Postgres: ``SELECT … FOR UPDATE SKIP LOCKED`` then UPDATE.
    SQLite (dev only): plain SELECT + UPDATE; not safe under concurrent workers.
    """
    cutoff = now or datetime.now(timezone.utc)
    stmt = (
        select(ScheduledPost)
        .where(ScheduledPost.status == "scheduled")
        .where(ScheduledPost.scheduled_for <= cutoff)
        .order_by(ScheduledPost.scheduled_for.asc())
        .limit(limit)
    )
    if _is_postgres(session):
        stmt = stmt.with_for_update(skip_locked=True)

    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return []

    ids = [r.id for r in rows]
    await session.execute(
        update(ScheduledPost)
        .where(ScheduledPost.id.in_(ids))
        .values(status="posting", worker_id=worker_id, locked_at=cutoff)
    )
    await session.commit()
    return rows


async def mark_published(
    session: AsyncSession, post_id: str, *, error: str | None = None
) -> None:
    new_status = "failed" if error else "published"
    await session.execute(
        update(ScheduledPost)
        .where(ScheduledPost.id == post_id)
        .values(status=new_status, last_error=error,
                attempts=ScheduledPost.attempts + 1)
    )
    await session.commit()
