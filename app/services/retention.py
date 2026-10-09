"""Retention sweep (FR-013): retire clips older than ``retention_days`` and
delete their rendered files.

The row is retired and committed *before* any file is deleted, so a failed
commit never leaves a live clip pointing at missing files. Only the files the
pipeline writes per clip (``output_path``, ``thumbnail_path``) are deleted;
jobs and their source video (``jobs.video_path``, needed for re-render) are
never touched.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ClipRecord

log = logging.getLogger(__name__)


async def sweep_expired_clips(
    factory: async_sessionmaker[AsyncSession],
    *,
    retention_days: int,
    now: datetime,
) -> list[str]:
    """Retire expired live clips, then delete their files.

    ``now`` must be timezone-aware; it is converted to UTC because SQLite
    stores timestamps as naive wall-clock strings. Returns the retired clip
    ids. A file that cannot be deleted is logged and skipped; a database
    error propagates (nothing has been deleted at that point).
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    cutoff = now.astimezone(UTC) - timedelta(days=retention_days)

    async with factory() as session:
        result = await session.execute(
            update(ClipRecord)
            .where(ClipRecord.created_at < cutoff)
            .where(ClipRecord.retired.is_(False))
            .values(retired=True)
            .returning(ClipRecord.id, ClipRecord.output_path, ClipRecord.thumbnail_path)
        )
        rows = result.all()
        await session.commit()

    for clip_id, *paths in rows:
        for raw in paths:
            if not raw:
                continue
            try:
                Path(raw).unlink(missing_ok=True)
            except OSError:
                log.exception("retention: could not delete %s (clip %s)", raw, clip_id)
    return [row[0] for row in rows]
