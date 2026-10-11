#!/usr/bin/env python
"""Backfill ``jobs.video_path`` for jobs created before it existed (T028 b).

Migration ``o3p4q5r6s7t8`` added the nullable ``jobs.video_path`` column, the
saved source a single-clip re-render reads. Jobs completed before it have
NULL there, so ``POST /clips/{id}/rerender`` answers 409 "source video not
retained" for every one of their clips. This script recovers the path where
it can be recovered unambiguously.

For each ``completed`` job whose ``video_path`` is NULL:

* ``upload://<path>`` URL: that path, if the file still exists.
* otherwise: the job folder is the parent of the ``clips/`` folder its live
  (non-retired) clips were rendered into (``<slug>-<job8>`` since PR #42,
  ``<slug>`` before). It must hold EXACTLY ONE top-level video file
  (``.mp4 .mkv .webm .mov .m4v``); ``clips/`` and ``exports/`` are not
  searched. None, or more than one, and the job is skipped with the reason.

Dry run by default: prints one row per job (id, URL, matched path or the
reason it was skipped) and writes nothing. ``--apply`` writes the matches. A
non-NULL ``video_path`` is never overwritten, so running it twice is a no-op.

Usage
-----
    python -m scripts.backfill_job_video_path [--database-url URL] [--apply]

``--database-url`` defaults to the app's ``settings.db_url``
(``YTVIDEO_DB_URL``).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine  # noqa: E402

from app.db.models import ClipRecord, JobRecord  # noqa: E402
from app.settings import settings  # noqa: E402

UPLOAD_SCHEME = "upload://"
VIDEO_SUFFIXES = frozenset({".mp4", ".mkv", ".webm", ".mov", ".m4v"})


@dataclass(frozen=True)
class Decision:
    """What the backfill does with one job: ``path`` set, or ``reason``."""

    job_id: str
    url: str
    path: str | None
    reason: str = ""


def resolve_source(
    url: str, clip_output_paths: Sequence[str]
) -> tuple[str | None, str]:
    """The job's source video, or ``(None, reason)`` when it is not certain.

    Args:
        url: the job's URL (``upload://<path>`` for uploads).
        clip_output_paths: ``output_path`` of the job's live clips.
    """
    if url.startswith(UPLOAD_SCHEME):
        upload = Path(url[len(UPLOAD_SCHEME) :])
        if upload.is_file():
            return str(upload), ""
        return None, f"upload file missing: {upload}"

    clips_dirs = {Path(p).parent for p in clip_output_paths if p}
    if not clips_dirs:
        return None, "no live clip with an output path"
    folders = {d.parent for d in clips_dirs}
    if len(folders) > 1:
        return None, f"clips span {len(folders)} job folders"
    folder = folders.pop()
    if not folder.is_dir():
        return None, f"job folder missing: {folder}"

    # Top level only: rendered clips (clips/) and exports (exports/) are
    # videos too, but never the source.
    videos = sorted(
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
    )
    if not videos:
        return None, f"no video file in {folder}"
    if len(videos) > 1:
        return None, f"ambiguous: {len(videos)} video files in {folder}"
    return str(videos[0]), ""


async def _decide(conn: AsyncConnection) -> list[Decision]:
    jobs = (
        await conn.execute(
            select(JobRecord.id, JobRecord.youtube_url)
            .where(JobRecord.status == "completed")
            .where(JobRecord.video_path.is_(None))
            .order_by(JobRecord.created_at, JobRecord.id)
        )
    ).all()
    decisions: list[Decision] = []
    for job_id, url in jobs:
        outputs = (
            (
                await conn.execute(
                    select(ClipRecord.output_path)
                    .where(ClipRecord.job_id == job_id)
                    .where(ClipRecord.retired == False)  # noqa: E712
                    .where(ClipRecord.output_path.is_not(None))
                )
            )
            .scalars()
            .all()
        )
        path, reason = resolve_source(url, outputs)
        decisions.append(Decision(job_id=job_id, url=url, path=path, reason=reason))
    return decisions


async def backfill(database_url: str, *, apply: bool) -> tuple[list[Decision], int]:
    """Decide every candidate job; with ``apply``, write the matches.

    Returns:
        The decisions and the number of rows written (always 0 without
        ``apply``).
    """
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            decisions = await _decide(conn)
            written = 0
            if apply:
                for d in decisions:
                    if d.path is None:
                        continue
                    result = await conn.execute(
                        update(JobRecord)
                        .where(JobRecord.id == d.job_id)
                        # Never overwrite a recorded source.
                        .where(JobRecord.video_path.is_(None))
                        # A backfill is not a job update: keep updated_at.
                        .values(video_path=d.path, updated_at=JobRecord.updated_at)
                    )
                    written += result.rowcount or 0
                await conn.commit()
            return decisions, written
    finally:
        await engine.dispose()


def format_report(decisions: Sequence[Decision], *, apply: bool, written: int) -> str:
    """A plain-text table of the decisions plus a summary line."""
    rows = [("JOB ID", "URL", "RESULT")]
    for d in decisions:
        rows.append((d.job_id, d.url, d.path if d.path else f"skipped: {d.reason}"))
    widths = [max(len(r[i]) for r in rows) for i in range(2)]
    lines = [
        "APPLY: writing matches"
        if apply
        else "DRY RUN: nothing is written; pass --apply to write the matches",
        f"{len(decisions)} job(s) with no video_path",
    ]
    if decisions:
        lines.append("")
        for job_id, url, result in rows:
            lines.append(f"{job_id:<{widths[0]}}  {url:<{widths[1]}}  {result}")
    matched = sum(1 for d in decisions if d.path)
    lines.append("")
    lines.append(
        f"matched {matched}, skipped {len(decisions) - matched}, written {written}"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill jobs.video_path for jobs created before it existed."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy async URL (default: the app's settings.db_url / YTVIDEO_DB_URL)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the matches (default: dry run, nothing written)",
    )
    args = parser.parse_args(argv)
    database_url = args.database_url or settings.db_url
    decisions, written = asyncio.run(backfill(database_url, apply=args.apply))
    print(format_report(decisions, apply=args.apply, written=written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
