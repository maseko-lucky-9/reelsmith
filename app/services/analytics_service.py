"""Per-platform analytics snapshots (W3.3).

Stores rolling time-series of per-clip metrics into
``clip_analytics_snapshots``. Each snapshot is an immutable record;
the dashboard reads the latest per (clip, platform) pair and the
delta vs the prior one.

Real platform Insights API calls live in
``app/services/social/<platform>.py`` analytics methods (a follow-up
patch). This module is the persistence + aggregation layer that
those callers feed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClipAnalyticsSnapshot


@dataclass(frozen=True)
class AnalyticsRecord:
    impressions: int = 0
    views: int = 0
    watch_time_seconds: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0


async def record_snapshot(
    session: AsyncSession,
    *,
    clip_id: str,
    platform: str,
    external_post_id: str,
    metrics: AnalyticsRecord,
    captured_at: datetime | None = None,
) -> ClipAnalyticsSnapshot:
    snap = ClipAnalyticsSnapshot(
        clip_id=clip_id,
        platform=platform,
        external_post_id=external_post_id,
        impressions=metrics.impressions,
        views=metrics.views,
        watch_time_seconds=metrics.watch_time_seconds,
        likes=metrics.likes,
        comments=metrics.comments,
        shares=metrics.shares,
        captured_at=captured_at or datetime.now(timezone.utc),
    )
    session.add(snap)
    await session.commit()
    await session.refresh(snap)
    return snap


async def latest_per_platform(
    session: AsyncSession, clip_id: str
) -> dict[str, ClipAnalyticsSnapshot]:
    """Return {platform: most-recent snapshot} for the clip."""
    rows = (
        await session.execute(
            select(ClipAnalyticsSnapshot)
            .where(ClipAnalyticsSnapshot.clip_id == clip_id)
            .order_by(
                ClipAnalyticsSnapshot.platform.asc(),
                desc(ClipAnalyticsSnapshot.captured_at),
            )
        )
    ).scalars().all()

    out: dict[str, ClipAnalyticsSnapshot] = {}
    for snap in rows:
        if snap.platform not in out:  # rows are platform-grouped + desc
            out[snap.platform] = snap
    return out


async def aggregate_for_clip(
    session: AsyncSession, clip_id: str
) -> AnalyticsRecord:
    """Sum metrics across the latest-per-platform snapshots."""
    latest = await latest_per_platform(session, clip_id)
    if not latest:
        return AnalyticsRecord()
    totals = AnalyticsRecord(
        impressions=sum(s.impressions for s in latest.values()),
        views=sum(s.views for s in latest.values()),
        watch_time_seconds=sum(s.watch_time_seconds for s in latest.values()),
        likes=sum(s.likes for s in latest.values()),
        comments=sum(s.comments for s in latest.values()),
        shares=sum(s.shares for s in latest.values()),
    )
    return totals
