"""Retention (FR-013): the sweeps the lifespan janitor runs on every tick.

1. ``sweep_expired_clips`` retires clips older than ``retention_days`` and
   deletes their rendered files (``output_path``, ``thumbnail_path``).
2. ``sweep_unused_sources`` removes a job's source video (``jobs.video_path``),
   its ``<stem>.words.json`` sidecar and its emptied per-job folders once no
   one can use them: the job is terminal, has no live clip, has been idle for
   ``retention_days`` and for the last hour, and has no reprompt in flight
   (T033). ``jobs.video_path`` is cleared, so a later re-render or reprompt
   answers 409 "source video not retained".
3. ``sweep_retired_files`` deletes the files of retired clips that are still
   on disk (``JobStore.retire_clips``, used by a reprompt, deletes none) once
   each file is older than ``retired_files_grace_hours`` (T033).

Shared rules. A row is changed and committed *before* any file is deleted,
so a failed commit never leaves a row pointing at a missing file. A file a
live clip still references is kept: rows written before per-job output
folders (T030) can share one path across jobs. Sweeps 2 and 3 delete only
below a managed root (``managed_roots``), after resolving symlinks and
``..``, and remove a folder only when it is empty (``rmdir``). A file or
folder that cannot be deleted is logged and skipped; a database error
propagates. ``exports/`` copies and manifests, and ``_tmp`` leftovers, are
not swept.
"""

from __future__ import annotations

import errno
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ClipRecord, JobRecord
from app.services.segment_discovery import words_sidecar_path
from app.settings import settings

log = logging.getLogger(__name__)

# Job statuses after which no pipeline will read the source again. Every
# other status (``pending``, ``running``: app.bus.job_store's
# INTERRUPTIBLE_STATUSES) means a pipeline may still need it.
TERMINAL_JOB_STATUSES: tuple[str, ...] = ("completed", "failed")

# A re-render has no in-flight registry (unlike a reprompt), so a source is
# only removed once neither the job row nor any of its clip rows changed for
# this long. A re-render rewrites its clip row; the clip sweep's retire
# bumps it too, so a source outlives its last clip by at least this much.
QUIET_PERIOD = timedelta(hours=1)

# Where uploads were stored before T031; must match
# app/services/platforms/upload.py::_LEGACY_UPLOAD_ROOT.
LEGACY_UPLOAD_ROOT = Path("/tmp/yt/uploads")

InFlight = Callable[[str], bool]


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
        candidates = {raw for _, *paths in rows for raw in paths if raw}
        still_live: set[str] = set()
        if candidates:
            live = await session.execute(
                select(ClipRecord.output_path, ClipRecord.thumbnail_path)
                .where(ClipRecord.retired.is_(False))
                .where(
                    or_(
                        ClipRecord.output_path.in_(candidates),
                        ClipRecord.thumbnail_path.in_(candidates),
                    )
                )
            )
            still_live = {raw for pair in live.all() for raw in pair if raw}
        await session.commit()

    for clip_id, *paths in rows:
        for raw in paths:
            if not raw:
                continue
            if raw in still_live:
                log.warning(
                    "retention: kept %s (clip %s): a live clip still uses it", raw, clip_id
                )
                continue
            try:
                Path(raw).unlink(missing_ok=True)
            except OSError:
                log.exception("retention: could not delete %s (clip %s)", raw, clip_id)
    return [row[0] for row in rows]


# ── Managed roots and path checks ─────────────────────────────────────────────


def managed_roots() -> tuple[Path, ...]:
    """The resolved folders the sweeps may delete below: the download path,
    its ``uploads`` folder and the legacy upload root. A root itself is
    never removed, so ``uploads`` stays even when it is empty."""
    download = Path(settings.default_download_path)
    return tuple(
        path.resolve() for path in (download, download / "uploads", LEGACY_UPLOAD_ROOT)
    )


def _resolve(raw: str) -> Path | None:
    """``raw`` with symlinks and ``..`` resolved, or None if it cannot be."""
    try:
        return Path(raw).resolve()
    except (OSError, RuntimeError):
        log.warning(
            "retention: kept %s: its path cannot be resolved", raw, exc_info=True
        )
        return None


def _is_managed(path: Path, roots: Sequence[Path]) -> bool:
    """True when the resolved ``path`` lies below a root and is not a root."""
    return path not in roots and any(path.is_relative_to(root) for root in roots)


def _unlink(path: Path, owner: str) -> bool:
    """Delete one file; a failure is logged and reported as False."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.exception("retention: could not delete %s (%s)", path, owner)
        return False
    return True


def _remove_empty_dirs(folders: Iterable[Path]) -> None:
    """``rmdir`` each folder, deepest first; a folder that still holds
    anything is kept as it is."""
    for folder in sorted(set(folders), key=lambda p: len(p.parts), reverse=True):
        try:
            folder.rmdir()
        except FileNotFoundError:
            continue
        except OSError as e:
            if e.errno in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOTDIR):
                log.debug("retention: kept folder %s: not empty", folder)
            else:
                log.exception("retention: could not remove folder %s", folder)


# ── Unused source videos ──────────────────────────────────────────────────────


async def sweep_unused_sources(
    factory: async_sessionmaker[AsyncSession],
    *,
    retention_days: int,
    now: datetime,
    roots: Sequence[Path],
    in_flight: InFlight,
) -> list[str]:
    """Remove the sources nobody can use any more; return those job ids.

    A job's source, its sidecar and its emptied per-job folders go when all
    hold: the job's status is in ``TERMINAL_JOB_STATUSES``; it has no live
    clip; its last activity (``updated_at``, else ``created_at``) is older
    than ``retention_days`` and than ``QUIET_PERIOD``; none of its clip rows
    changed within ``QUIET_PERIOD``; ``in_flight(job_id)`` is False; the
    source resolves below one of ``roots``; and no other job references the
    same file (by its path or its resolved path). ``jobs.video_path`` is set
    to NULL and committed before anything is deleted.

    Folders removed when empty: the source's own folder and its ``clips``
    (a URL job's ``<slug>-<job8>``), and the folder of each clip file of the
    job and that folder's parent (an upload job's ``upload_video-<job8>``).
    ``now`` must be timezone-aware.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(UTC)
    age_cutoff = now_utc - timedelta(days=retention_days)
    quiet_cutoff = now_utc - QUIET_PERIOD
    roots = tuple(Path(root).resolve() for root in roots)

    last_activity = func.coalesce(JobRecord.updated_at, JobRecord.created_at)
    has_live_clip = exists().where(
        ClipRecord.job_id == JobRecord.id, ClipRecord.retired.is_(False)
    )
    has_recent_clip = exists().where(
        ClipRecord.job_id == JobRecord.id, ClipRecord.updated_at >= quiet_cutoff
    )
    # Applied to the candidate SELECT and again to each clearing UPDATE.
    unused = (
        JobRecord.video_path.is_not(None),
        JobRecord.status.in_(TERMINAL_JOB_STATUSES),
        ~has_live_clip,
        last_activity < age_cutoff,
        last_activity < quiet_cutoff,
        ~has_recent_clip,
    )

    cleared: list[tuple[str, Path]] = []
    folders: list[Path] = []
    async with factory() as session:
        candidates = (
            await session.execute(
                select(JobRecord.id, JobRecord.video_path)
                .where(*unused)
                .order_by(JobRecord.created_at, JobRecord.id)
            )
        ).all()
        if not candidates:
            return []
        references = [
            (job_id, raw, _resolve(raw))
            for job_id, raw in (
                await session.execute(
                    select(JobRecord.id, JobRecord.video_path).where(
                        JobRecord.video_path.is_not(None)
                    )
                )
            ).all()
        ]

        for job_id, raw in candidates:
            source = _resolve(raw)
            if source is None:
                continue
            # Debug, not warning: these are steady states, seen every tick.
            if not _is_managed(source, roots):
                log.debug(
                    "retention: kept source %s (job %s): outside the managed folders",
                    raw,
                    job_id,
                )
                continue
            if any(
                other_id != job_id and (other_raw == raw or other_source == source)
                for other_id, other_raw, other_source in references
            ):
                log.debug(
                    "retention: kept source %s (job %s): another job uses it",
                    raw,
                    job_id,
                )
                continue
            if in_flight(job_id):
                continue
            result = await session.execute(
                update(JobRecord)
                .where(JobRecord.id == job_id, JobRecord.video_path == raw, *unused)
                .values(video_path=None)
                .returning(JobRecord.id)
            )
            if result.first() is None:
                continue
            cleared.append((job_id, source))
            if _is_managed(source.parent, roots):
                folders += [source.parent / "clips", source.parent]

        if cleared:
            clip_paths = (
                await session.execute(
                    select(ClipRecord.output_path, ClipRecord.thumbnail_path).where(
                        ClipRecord.job_id.in_([job_id for job_id, _ in cleared])
                    )
                )
            ).all()
            for raw in {raw for pair in clip_paths for raw in pair if raw}:
                path = _resolve(raw)
                if path is None:
                    continue
                folders += [
                    folder
                    for folder in (path.parent, path.parent.parent)
                    if _is_managed(folder, roots)
                ]
        await session.commit()

    for job_id, source in cleared:
        for path in (source, words_sidecar_path(str(source))):
            _unlink(path, f"source of job {job_id}")
        log.info("retention: removed the unused source %s (job %s)", source, job_id)
    _remove_empty_dirs(folders)
    return [job_id for job_id, _ in cleared]


# ── Files of retired clips ────────────────────────────────────────────────────


async def sweep_retired_files(
    factory: async_sessionmaker[AsyncSession],
    *,
    grace_hours: float,
    now: datetime,
    roots: Sequence[Path],
    in_flight: InFlight,
) -> list[str]:
    """Delete the leftover files of retired clips; return the deleted paths.

    A retired clip's ``output_path`` or ``thumbnail_path`` is deleted when the
    file's mtime is more than ``grace_hours`` before ``now``, it resolves
    below one of ``roots``, and neither a live clip nor any clip of a job
    with ``in_flight(job_id)`` references it (by path or resolved path): a
    reprompt's new clips stay retired (hidden) until its swap. Rows are not
    changed. ``now`` must be timezone-aware.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    cutoff = (now.astimezone(UTC) - timedelta(hours=grace_hours)).timestamp()
    roots = tuple(Path(root).resolve() for root in roots)

    async with factory() as session:
        retired = (
            await session.execute(
                select(
                    ClipRecord.id,
                    ClipRecord.job_id,
                    ClipRecord.output_path,
                    ClipRecord.thumbnail_path,
                )
                .where(ClipRecord.retired.is_(True))
                .where(
                    or_(
                        ClipRecord.output_path.is_not(None),
                        ClipRecord.thumbnail_path.is_not(None),
                    )
                )
                .order_by(ClipRecord.created_at, ClipRecord.id)
            )
        ).all()
        if not retired:
            return []
        live = (
            await session.execute(
                select(ClipRecord.output_path, ClipRecord.thumbnail_path).where(
                    ClipRecord.retired.is_(False)
                )
            )
        ).all()

    # Files no retired clip may take with it: those of live clips, and those
    # of every clip of a job with a reprompt in flight (its new clips are
    # retired, i.e. hidden, until the swap).
    keep_raw = {raw for pair in live for raw in pair if raw} | {
        raw
        for _, job_id, *paths in retired
        if in_flight(job_id)
        for raw in paths
        if raw
    }
    keep = {path for raw in keep_raw if (path := _resolve(raw)) is not None}
    seen: set[Path] = set()
    deleted: list[str] = []
    for clip_id, _, *paths in retired:
        for raw in paths:
            if not raw or raw in keep_raw:
                continue
            path = _resolve(raw)
            if (
                path is None
                or path in seen
                or path in keep
                or not _is_managed(path, roots)
            ):
                continue
            seen.add(path)
            try:
                mtime = path.stat().st_mtime
            except FileNotFoundError:
                continue
            except OSError:
                log.exception(
                    "retention: could not stat %s (retired clip %s)", path, clip_id
                )
                continue
            if mtime >= cutoff:
                continue
            if _unlink(path, f"retired clip {clip_id}"):
                deleted.append(str(path))
    return deleted


# ── The janitor tick ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RetentionReport:
    retired_clip_ids: list[str]
    cleaned_job_ids: list[str]
    deleted_files: list[str]


async def run_retention_sweeps(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    in_flight: InFlight | None = None,
) -> RetentionReport:
    """One janitor tick: the three sweeps, in order, configured by settings.

    ``in_flight`` defaults to the orchestrator's reprompt registry, which is
    in-process like the janitor itself.
    """
    if in_flight is None:
        # Imported here: a service module does not load the pipeline module.
        from app.workers.orchestrator import reprompt_in_flight

        in_flight = reprompt_in_flight
    roots = managed_roots()
    report = RetentionReport(
        retired_clip_ids=await sweep_expired_clips(
            factory, retention_days=settings.retention_days, now=now
        ),
        cleaned_job_ids=await sweep_unused_sources(
            factory,
            retention_days=settings.retention_days,
            now=now,
            roots=roots,
            in_flight=in_flight,
        ),
        deleted_files=await sweep_retired_files(
            factory,
            grace_hours=settings.retired_files_grace_hours,
            now=now,
            roots=roots,
            in_flight=in_flight,
        ),
    )
    if report.retired_clip_ids or report.cleaned_job_ids or report.deleted_files:
        log.info(
            "Retention: retired %d clip(s), removed %d unused source(s), "
            "deleted %d retired clip file(s)",
            len(report.retired_clip_ids),
            len(report.cleaned_job_ids),
            len(report.deleted_files),
        )
    return report
