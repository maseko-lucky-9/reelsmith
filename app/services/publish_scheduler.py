"""APScheduler scaffold for publish_jobs (W1.4).

Polls ``publish_jobs`` for rows whose ``schedule_at`` has elapsed and
flips them ``pending`` → ``queued``. The actual platform POST is
performed by ``social_publish_service`` (W1.5) and consumed via the
event bus.

Design notes:

* Polling-only; no broker. Interval is settings-driven
  (``YTVIDEO_SCHEDULER_POLL_SECONDS`` default 30s).
* Idempotent flip: ``UPDATE … WHERE status='pending' AND schedule_at<=now``
  with ``UPDATE … RETURNING`` (Postgres) or follow-up SELECT (SQLite).
* Lifecycle managed by ``app.main`` startup/shutdown events.
* W3 retires this in favour of a Postgres SKIP LOCKED worker that
  reads the same table.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import PublishJob

log = logging.getLogger(__name__)

# Type alias: caller-supplied async sink invoked once per newly-queued job.
QueuedSink = Callable[[str], Awaitable[None]]


async def promote_due_jobs(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Flip ``pending`` rows whose ``schedule_at`` has elapsed to ``queued``.

    Returns the IDs that were promoted. Caller is responsible for
    handing each ID off to ``social_publish_service``.
    """
    cutoff = now or datetime.now(timezone.utc)

    # Two-step (SQLite-safe): SELECT ids that are due, then UPDATE them.
    # Postgres could use UPDATE … RETURNING in one round-trip; we keep the
    # portable form for SQLite parity tests. W3 worker upgrades to SKIP
    # LOCKED for concurrency safety.
    rows = await session.execute(
        select(PublishJob.id)
        .where(PublishJob.status == "pending")
        .where(PublishJob.schedule_at.is_not(None))
        .where(PublishJob.schedule_at <= cutoff)
        .order_by(PublishJob.schedule_at.asc())
        .limit(50)
    )
    due_ids = [r[0] for r in rows.all()]
    if not due_ids:
        return []

    await session.execute(
        update(PublishJob)
        .where(PublishJob.id.in_(due_ids))
        .where(PublishJob.status == "pending")
        .values(status="queued", updated_at=cutoff)
    )
    await session.commit()
    return due_ids


class PublishScheduler:
    """APScheduler-backed poller. One instance per app."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_seconds: int = 30,
        sink: QueuedSink | None = None,
    ) -> None:
        self._factory = session_factory
        self._poll_seconds = poll_seconds
        self._sink = sink
        self._scheduler = None  # lazy import; APScheduler is opt-in

    async def start(self) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        if self._scheduler is not None:  # idempotent
            return
        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self._tick,
            trigger="interval",
            seconds=self._poll_seconds,
            id="publish_scheduler_tick",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.start()
        log.info("publish_scheduler started (every %ss)", self._poll_seconds)

    async def shutdown(self) -> None:
        if self._scheduler is None:
            return
        self._scheduler.shutdown(wait=False)
        self._scheduler = None
        log.info("publish_scheduler stopped")

    async def _tick(self) -> None:
        try:
            async with self._factory() as session:
                ids = await promote_due_jobs(session)
        except Exception:  # noqa: BLE001
            log.exception("publish_scheduler tick failed")
            return

        if not ids:
            return
        log.info("publish_scheduler promoted %d job(s)", len(ids))
        if self._sink is not None:
            for jid in ids:
                try:
                    await self._sink(jid)
                except Exception:  # noqa: BLE001
                    log.exception("publish_scheduler sink failed for %s", jid)
