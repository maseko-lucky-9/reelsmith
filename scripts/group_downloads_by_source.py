#!/usr/bin/env python
"""Move existing job folders into one folder per source (spec 018).

New jobs are written to ``<download_path>/<YouTube|TikTok|Facebook|Instagram> Videos/
<slug>-<job8>/`` (``app.services.folder_service.SOURCE_FOLDERS``). This script
moves the job folders created before that, which sit directly under the
download path, into the same layout and rewrites every database path that
points into them:

* ``jobs.video_path`` and the paths inside ``jobs.state``
* ``chapters.artifacts``
* ``clips.output_path``, ``thumbnail_path``, ``ai_hook_audio_path``,
  ``captions_burnt_path``
* ``clip_edits.timeline``

A job is moved only when its ``source`` has a group folder, its
``state.destination_folder`` is a folder directly under the download path, and
the target does not exist yet. Anything else is reported and left alone, among
them a folder several jobs share (jobs from before per-job folders); a folder
with no job row is never touched. Each job is one database transaction and the
folder is moved back if it fails. If the run is killed between the move and the
commit, the next run sees the folder at its target with the rows still old and
only rewrites the rows. A second run is a no-op. One failing job is reported and
the run goes on.

Dry run by default. Stop the API first and take a ``pg_dump`` before ``--apply``.

Usage
-----
    python -m scripts.group_downloads_by_source [--database-url URL]
        [--download-path DIR] [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine  # noqa: E402

from app.db.models import ChapterRecord, ClipEdit, ClipRecord, JobRecord  # noqa: E402
from app.services.folder_service import source_folder  # noqa: E402
from app.settings import settings  # noqa: E402

CLIP_PATH_COLUMNS = ("output_path", "thumbnail_path", "ai_hook_audio_path", "captions_burnt_path")


class MoveFailedError(Exception):
    """Some jobs failed (their folders were put back); the others were moved."""

    def __init__(self, moved: int, failures: Sequence[str]) -> None:
        super().__init__(f"{moved} moved, {len(failures)} failed: " + "; ".join(failures))
        self.moved = moved
        self.failures = tuple(failures)


@dataclass(frozen=True)
class Move:
    """One job folder: moved to ``target``, or left because of ``reason``."""

    job_id: str
    source: str
    folder: str
    target: str | None
    reason: str = ""
    # The folder is already at ``target`` (an earlier run died before the commit):
    # only the rows are rewritten.
    rows_only: bool = False


def rewrite_str(value: str, old: str, new: str) -> str:
    """``value`` with the folder prefix ``old`` replaced by ``new``.

    Only a whole path component matches: ``/a/b`` rewrites ``/a/b`` and
    ``/a/b/x`` but not ``/a/bc``.
    """
    if value == old:
        return new
    if value.startswith(old + os.sep):
        return new + value[len(old) :]
    return value


def rewrite_json(value: Any, old: str, new: str) -> Any:
    """``value`` (a JSON document) with every string's ``old`` prefix replaced."""
    if isinstance(value, str):
        return rewrite_str(value, old, new)
    if isinstance(value, list):
        return [rewrite_json(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: rewrite_json(item, old, new) for key, item in value.items()}
    return value


def plan_move(job_id: str, source: str | None, folder: str | None, base: Path) -> Move:
    """Decide one job: where its folder goes, or why it stays."""
    group = source_folder(source)
    if group is None:
        return Move(job_id, source or "", folder or "", None, "source has no group folder")
    if not folder:
        return Move(job_id, source or "", "", None, "no destination_folder recorded")
    current = Path(folder)
    if current.parent != base:
        return Move(job_id, source or "", folder, None, "not directly under the download path")
    target = base / group / current.name
    if not current.is_dir():
        if target.is_dir():
            return Move(job_id, source or "", folder, str(target), rows_only=True)
        return Move(job_id, source or "", folder, None, "folder is missing on disk")
    if target.exists():
        return Move(job_id, source or "", folder, None, f"target exists: {target}")
    return Move(job_id, source or "", folder, str(target))


async def _plan(conn: AsyncConnection, base: Path) -> list[Move]:
    rows = (
        await conn.execute(
            select(JobRecord.id, JobRecord.source, JobRecord.state).order_by(
                JobRecord.created_at, JobRecord.id
            )
        )
    ).all()
    folders = [(state or {}).get("destination_folder") for _, _, state in rows]
    sharing = {f for f in folders if f and folders.count(f) > 1}
    moves: list[Move] = []
    for (job_id, source, _), folder in zip(rows, folders, strict=True):
        if folder in sharing:
            moves.append(
                Move(job_id, source or "", folder, None, "folder shared by several jobs")
            )
        else:
            moves.append(plan_move(job_id, source, folder, base))
    return moves


async def _rewrite_rows(conn: AsyncConnection, move: Move) -> None:
    assert move.target is not None
    old, new = move.folder, move.target
    job = (
        await conn.execute(
            select(JobRecord.video_path, JobRecord.state).where(JobRecord.id == move.job_id)
        )
    ).one()
    # Keep updated_at: moving files is not a job update.
    await conn.execute(
        update(JobRecord)
        .where(JobRecord.id == move.job_id)
        .values(
            video_path=rewrite_str(job.video_path, old, new) if job.video_path else None,
            state=rewrite_json(job.state, old, new),
            updated_at=JobRecord.updated_at,
        )
    )
    chapters = (
        await conn.execute(
            select(ChapterRecord.id, ChapterRecord.artifacts).where(
                ChapterRecord.job_id == move.job_id
            )
        )
    ).all()
    for chapter_id, artifacts in chapters:
        if artifacts:
            await conn.execute(
                update(ChapterRecord)
                .where(ChapterRecord.id == chapter_id)
                .values(artifacts=rewrite_json(artifacts, old, new))
            )
    clips = (
        await conn.execute(
            select(ClipRecord.id, *(getattr(ClipRecord, c) for c in CLIP_PATH_COLUMNS)).where(
                ClipRecord.job_id == move.job_id
            )
        )
    ).all()
    for clip in clips:
        values = {
            column: rewrite_str(getattr(clip, column), old, new)
            for column in CLIP_PATH_COLUMNS
            if getattr(clip, column)
        }
        if values:
            await conn.execute(
                update(ClipRecord)
                .where(ClipRecord.id == clip.id)
                .values(**values, updated_at=ClipRecord.updated_at)
            )
        edit = (
            await conn.execute(
                select(ClipEdit.id, ClipEdit.timeline).where(
                    ClipEdit.clip_id == clip.id
                )
            )
        ).first()
        if edit is not None and edit.timeline:
            await conn.execute(
                update(ClipEdit)
                .where(ClipEdit.id == edit.id)
                .values(
                    timeline=rewrite_json(edit.timeline, old, new),
                    updated_at=ClipEdit.updated_at,
                )
            )


async def group_downloads(
    database_url: str, download_path: Path, *, apply: bool
) -> tuple[list[Move], int]:
    """Plan every job; with ``apply``, move the folders and rewrite the rows.

    Returns:
        The decisions and the number of job folders moved (0 without ``apply``).
    """
    base = download_path.resolve()
    engine = create_async_engine(database_url)
    moved = 0
    failures: list[str] = []
    try:
        async with engine.connect() as conn:
            moves = await _plan(conn, base)
        if apply:
            for move in moves:
                if move.target is None:
                    continue
                if not move.rows_only:
                    Path(move.target).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(move.folder, move.target)
                try:
                    async with engine.begin() as conn:
                        await _rewrite_rows(conn, move)
                except Exception as exc:
                    if not move.rows_only:
                        shutil.move(move.target, move.folder)
                    failures.append(f"{move.job_id[:8]}: {exc}")
                    continue
                moved += 1
        if failures:
            raise MoveFailedError(moved, failures)
        return moves, moved
    finally:
        await engine.dispose()


def format_report(moves: Sequence[Move], *, apply: bool, moved: int) -> str:
    """A plain-text list of the decisions plus a summary line."""
    lines = [
        "APPLY: moving folders and rewriting paths"
        if apply
        else "DRY RUN: nothing is moved; pass --apply to move the folders",
        "",
    ]
    for move in moves:
        result = f"-> {move.target}" if move.target else f"skipped: {move.reason}"
        lines.append(f"{move.job_id[:8]}  {move.source or '-':<10}  {move.folder}  {result}")
    planned = sum(1 for move in moves if move.target)
    lines.append("")
    lines.append(f"{len(moves)} job(s): {planned} to move, {len(moves) - planned} left, moved {moved}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Move job folders into per-source folders.")
    parser.add_argument("--database-url", default=None, help="default: settings.db_url")
    parser.add_argument(
        "--download-path", default=None, help="default: settings.default_download_path"
    )
    parser.add_argument("--apply", action="store_true", help="move folders (default: dry run)")
    args = parser.parse_args(argv)
    try:
        moves, moved = asyncio.run(
            group_downloads(
                args.database_url or settings.db_url,
                Path(args.download_path or settings.default_download_path),
                apply=args.apply,
            )
        )
    except MoveFailedError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(format_report(moves, apply=args.apply, moved=moved))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
