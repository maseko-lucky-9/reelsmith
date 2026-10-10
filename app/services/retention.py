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
folders (T030) can share one path across jobs. A file or folder that cannot
be deleted is logged and skipped; a database error propagates. ``exports/``
copies and manifests, and ``_tmp`` leftovers, are not swept.

Safety of sweeps 2 and 3. Paths read from the database are untrusted: a value
with a NUL byte, a relative path or a ``..`` component is never used to
delete anything (``_checked``) and no filesystem call is made for it. The
others are resolved and must lie below a managed root (``managed_roots``).
Every delete then goes through ``delete_below_root``, which re-validates at
the moment of deletion instead of trusting that earlier check: the folder
chain is re-opened from the root without following symlinks, the entry must
not be a symlink, and a folder is removed only with ``rmdir`` (empty only).
"""

from __future__ import annotations

import errno
import logging
import os
import stat
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePath
from urllib.parse import urlparse

from sqlalchemy import and_, exists, func, or_, select, update
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
# URL scheme of a job made from an uploaded file (app/routers/uploads.py).
_UPLOAD_SCHEME = "upload://"

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


# ── Managed roots and untrusted paths ─────────────────────────────────────────


def managed_roots() -> tuple[Path, ...]:
    """The resolved folders the sweeps may delete below: the download path,
    its ``uploads`` folder and the legacy upload root. A root itself is
    never removed, so ``uploads`` stays even when it is empty."""
    download = Path(settings.default_download_path)
    return tuple(
        path.resolve() for path in (download, download / "uploads", LEGACY_UPLOAD_ROOT)
    )


def _is_unsafe(raw: str) -> bool:
    """A database path that must never reach the filesystem: a NUL byte, a
    relative path or a ``..`` component. A purely lexical test."""
    return "\x00" in raw or not os.path.isabs(raw) or ".." in PurePath(raw).parts


def _checked(raw: str | None) -> Path | None:
    """The resolved form of a database path, or None when it is unsafe
    (``_is_unsafe``: rejected without any filesystem call) or cannot be
    resolved."""
    if not raw or _is_unsafe(raw):
        return None
    try:
        return Path(raw).resolve()
    except (OSError, RuntimeError, ValueError):
        log.warning(
            "retention: kept %s: its path cannot be resolved", raw, exc_info=True
        )
        return None


def _aliases(raw: str) -> set[str]:
    """Every spelling of one database path to compare for "the same file":
    the stored string, its lexical normal form (absolute paths only) and,
    for a safe path, its resolved form. Used only to *keep* files, so an
    unsafe path still protects what it names."""
    names = {raw}
    if "\x00" not in raw and os.path.isabs(raw):
        names.add(os.path.normpath(raw))
    if (resolved := _checked(raw)) is not None:
        names.add(str(resolved))
    return names


def _is_managed(path: Path, roots: Sequence[Path]) -> bool:
    """True when the resolved ``path`` lies below a root and is not a root."""
    return path not in roots and any(path.is_relative_to(root) for root in roots)


# ── Deleting, re-validated at the moment of deletion ──────────────────────────

# With dir_fd support (macOS, Linux) the folder chain is walked from the root
# with O_NOFOLLOW, one openat() per component, and the entry is lstat'ed and
# removed relative to the last folder's descriptor: a folder swapped for a
# symlink at any depth fails the walk (ELOOP/ENOTDIR), and nothing outside
# the root can be reached. Without it (e.g. Windows) the parent is
# re-resolved and compared just before an lstat + unlink by path, which
# narrows the race window but cannot close it.
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_PINNED = (
    {os.open, os.stat, os.unlink, os.rmdir} <= os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
)


class _Refused(Exception):
    """The entry changed since the sweep checked it; it is left alone."""


def _owning_root(path: Path, roots: Sequence[Path]) -> Path | None:
    """The deepest root ``path`` lies strictly below; None if there is none,
    if ``path`` is a root, or if it is not absolute and normalised."""
    if not path.is_absolute() or ".." in path.parts or path in roots:
        return None
    owners = [root for root in roots if path.is_relative_to(root)]
    return max(owners, key=lambda root: len(root.parts), default=None)


def _verify(
    st: os.stat_result,
    path: Path,
    directory: bool,
    accept: Callable[[os.stat_result], bool] | None,
) -> bool:
    """Whether the lstat'ed entry may go. A symlink is refused; a folder
    where a file is expected raises, so it is logged like a failed unlink."""
    if stat.S_ISLNK(st.st_mode):
        raise _Refused("it is a symlink now")
    if directory and not stat.S_ISDIR(st.st_mode):
        raise _Refused("it is no longer a folder")
    if not directory and stat.S_ISDIR(st.st_mode):
        raise IsADirectoryError(errno.EISDIR, "a folder, not a file", str(path))
    return accept is None or accept(st)


def _open_folder(name: str | Path, dir_fd: int | None = None) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _Refused(f"{name} is no longer a real folder") from None
        raise


def _remove_pinned(
    path: Path,
    root: Path,
    directory: bool,
    accept: Callable[[os.stat_result], bool] | None,
) -> bool:
    *folders, name = path.relative_to(root).parts
    fd = _open_folder(root)
    try:
        for folder in folders:
            child = _open_folder(folder, dir_fd=fd)
            os.close(fd)
            fd = child
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if not _verify(st, path, directory, accept):
            return False
        if directory:
            os.rmdir(name, dir_fd=fd)
        else:
            os.unlink(name, dir_fd=fd)
        return True
    finally:
        os.close(fd)


def _remove_by_path(
    path: Path,
    root: Path,
    directory: bool,
    accept: Callable[[os.stat_result], bool] | None,
) -> bool:
    parent = path.parent
    if parent.resolve() != parent or not parent.is_relative_to(root):
        raise _Refused("its folder moved")
    if not _verify(os.lstat(path), path, directory, accept):
        return False
    if directory:
        os.rmdir(path)
    else:
        os.unlink(path)
    return True


def delete_below_root(
    path: Path,
    roots: Sequence[Path],
    *,
    owner: str,
    directory: bool = False,
    accept: Callable[[os.stat_result], bool] | None = None,
) -> bool:
    """Delete one file (or, with ``directory``, one empty folder) that lies
    strictly below one of the resolved ``roots``; True if it was deleted.

    Re-validated now, whatever the caller checked before: the path must be
    absolute, normalised and not a root; every folder from the root down
    must be a real folder (no symlink); the entry itself must not be a
    symlink; ``accept(lstat)``, if given, must agree. A folder is removed
    with ``rmdir`` only, so one that holds anything stays. Missing entries
    are not an error; refusals are logged as warnings and failures with
    their traceback. Never raises ``OSError``.
    """
    root = _owning_root(path, roots)
    if root is None:
        log.warning(
            "retention: refused %s (%s): not below a managed folder", path, owner
        )
        return False
    remove = _remove_pinned if _PINNED else _remove_by_path
    try:
        return remove(path, root, directory, accept)
    except FileNotFoundError:
        return False
    except _Refused as e:
        log.warning("retention: refused %s (%s): %s", path, owner, e)
        return False
    except OSError as e:
        if directory and e.errno in (errno.ENOTEMPTY, errno.EEXIST):
            log.debug("retention: kept folder %s (%s): not empty", path, owner)
        else:
            log.exception("retention: could not delete %s (%s)", path, owner)
        return False


def _remove_empty_dirs(folders: Iterable[Path], roots: Sequence[Path]) -> None:
    """Remove each folder that is empty, deepest first."""
    for folder in sorted(set(folders), key=lambda p: len(p.parts), reverse=True):
        delete_below_root(folder, roots, owner="emptied job folder", directory=True)


# ── Unused source videos ──────────────────────────────────────────────────────


async def _source_references(session: AsyncSession) -> list[tuple[str, set[str]]]:
    """Each job's claim on a source file, as ``_aliases`` names: its
    ``video_path`` and, for a job that can still run, the file its
    ``upload://`` URL names (``video_path`` is only set at the download
    step, e.g. for a queued retry of an old upload job)."""
    rows = (
        await session.execute(
            select(
                JobRecord.id,
                JobRecord.video_path,
                JobRecord.youtube_url,
                JobRecord.status,
            ).where(
                or_(
                    JobRecord.video_path.is_not(None),
                    and_(
                        JobRecord.status.not_in(TERMINAL_JOB_STATUSES),
                        JobRecord.youtube_url.startswith(_UPLOAD_SCHEME),
                    ),
                )
            )
        )
    ).all()
    references: list[tuple[str, set[str]]] = []
    for job_id, raw, url, status in rows:
        names = _aliases(raw) if raw else set()
        if status not in TERMINAL_JOB_STATUSES and url.startswith(_UPLOAD_SCHEME):
            names |= _aliases(urlparse(url).path)
        references.append((job_id, names))
    return references


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
    changed within ``QUIET_PERIOD``; the source is a safe path
    (``_checked``) that resolves below one of ``roots``; and, checked after
    the clearing UPDATE and just before the commit, ``in_flight(job_id)``
    is False and no other job references the same file
    (``_source_references``; jobs cleared in the same sweep do not count).
    ``jobs.video_path`` is set to NULL (put back, with ``updated_at``, when
    a late check fails) and committed before anything is deleted, through
    ``delete_below_root``. The clear is a compare-and-set: one conditional
    UPDATE per job (``video_path`` unchanged plus every condition above),
    and only a job whose UPDATE matched can lose its file. The registry
    is read once more after the commit, right before the delete; a
    reprompt that started in between keeps the file and gets
    ``video_path`` back.

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

    tentative: list[tuple[str, str, datetime, Path]] = []
    cleared: list[tuple[str, str, datetime, Path]] = []
    folders: dict[str, list[Path]] = {}
    async with factory() as session:
        candidates = (
            await session.execute(
                select(JobRecord.id, JobRecord.video_path, JobRecord.updated_at)
                .where(*unused)
                .order_by(JobRecord.created_at, JobRecord.id)
            )
        ).all()
        if not candidates:
            return []

        for job_id, raw, updated_at in candidates:
            source = _checked(raw)
            # Debug, not warning: these are steady states, seen every tick.
            if source is None or not _is_managed(source, roots):
                log.debug(
                    "retention: kept source %r (job %s): unsafe or outside the "
                    "managed folders",
                    raw,
                    job_id,
                )
                continue
            result = await session.execute(
                update(JobRecord)
                .where(JobRecord.id == job_id, JobRecord.video_path == raw, *unused)
                .values(video_path=None)
                .returning(JobRecord.id)
            )
            if result.first() is not None:
                tentative.append((job_id, raw, updated_at, source))

        # Read after the clearing UPDATEs, just before the commit: on SQLite
        # this transaction now holds the write lock, so no job can be queued
        # or changed until it commits. Jobs cleared above no longer count, so
        # idle jobs that share one source are cleaned together.
        references = await _source_references(session) if tentative else []
        for job_id, raw, updated_at, source in tentative:
            names = _aliases(raw)
            busy = in_flight(job_id)
            shared = any(
                other_id != job_id and names & other_names
                for other_id, other_names in references
            )
            if busy or shared:
                # Undo the clear, last activity included, so the job is not
                # treated as freshly active.
                await session.execute(
                    update(JobRecord)
                    .where(JobRecord.id == job_id)
                    .values(video_path=raw, updated_at=updated_at)
                )
                log.debug(
                    "retention: kept source %s (job %s): %s",
                    raw,
                    job_id,
                    "a reprompt is in flight" if busy else "another job uses it",
                )
                continue
            cleared.append((job_id, raw, updated_at, source))
            if _is_managed(source.parent, roots):
                folders[job_id] = [source.parent / "clips", source.parent]

        for job_id, *_ in cleared:
            clip_paths = (
                await session.execute(
                    select(ClipRecord.output_path, ClipRecord.thumbnail_path).where(
                        ClipRecord.job_id == job_id
                    )
                )
            ).all()
            for raw in {raw for pair in clip_paths for raw in pair if raw}:
                path = _checked(raw)
                if path is None:
                    continue
                folders.setdefault(job_id, []).extend(
                    folder
                    for folder in (path.parent, path.parent.parent)
                    if _is_managed(folder, roots)
                )
        await session.commit()

    # Only claims that won (their UPDATE matched and was committed) get here.
    # The registry is read once more, with no await between this check and
    # the deletes. A reprompt that started since then must have read the job
    # before the commit (after it, video_path is NULL and the reprompt router
    # answers 409); its source is kept and video_path put back below, so the
    # file is never deleted under it. A re-render has no registry: it needs
    # a live clip, which a won claim proves there was none of.
    removed: list[str] = []
    restore: list[tuple[str, str, datetime]] = []
    emptied: list[Path] = []
    for job_id, raw, updated_at, source in cleared:
        if in_flight(job_id):
            restore.append((job_id, raw, updated_at))
            continue
        for path in (source, words_sidecar_path(str(source))):
            delete_below_root(path, roots, owner=f"source of job {job_id}")
        log.info("retention: removed the unused source %s (job %s)", source, job_id)
        removed.append(job_id)
        emptied += folders.get(job_id, [])
    _remove_empty_dirs(emptied, roots)

    for job_id, raw, updated_at in restore:
        log.warning(
            "retention: kept source %s (job %s): a reprompt started while it was "
            "being removed",
            raw,
            job_id,
        )
        async with factory() as session:
            await session.execute(
                update(JobRecord)
                .where(JobRecord.id == job_id, JobRecord.video_path.is_(None))
                .values(video_path=raw, updated_at=updated_at)
            )
            await session.commit()
    return removed


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

    A retired clip's ``output_path`` or ``thumbnail_path`` is deleted when it
    is a safe path (``_checked``) resolving below one of ``roots``, neither a
    live clip nor any clip of a job with ``in_flight(job_id)`` references it
    (``_aliases``; a reprompt's new clips stay retired, i.e. hidden, until
    its swap), and the file's mtime, read at the moment of deletion, is more
    than ``grace_hours`` before ``now``. Right before each delete the clip
    row is read again: it must still be retired and no live clip may
    reference the file. Rows are not changed. ``now`` must
    be timezone-aware.
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

        # Files no retired clip may take with it: those of live clips, and
        # those of every clip of a job with a reprompt in flight.
        keep: set[str] = set()
        for raw in {raw for pair in live for raw in pair if raw}:
            keep |= _aliases(raw)
        for _, job_id, *paths in retired:
            if in_flight(job_id):
                for raw in paths:
                    if raw:
                        keep |= _aliases(raw)

        seen: set[Path] = set()
        deleted: list[str] = []
        for clip_id, _, *paths in retired:
            for raw in paths:
                names = _aliases(raw) if raw else set()
                if not raw or names & keep:
                    continue
                path = _checked(raw)
                if path is None or path in seen or not _is_managed(path, roots):
                    continue
                seen.add(path)
                if not os.path.lexists(path):
                    continue
                # Re-read right before the delete (no await between this
                # read and the delete): the clip must still be retired and
                # no live clip may use the file now.
                if not await _still_retired_and_unused(
                    session, clip_id, names | {str(path)}
                ):
                    log.debug(
                        "retention: kept %s (clip %s): back in use", path, clip_id
                    )
                    continue
                if delete_below_root(
                    path,
                    roots,
                    owner=f"retired clip {clip_id}",
                    accept=lambda st: st.st_mtime < cutoff,
                ):
                    deleted.append(str(path))
    return deleted


async def _still_retired_and_unused(
    session: AsyncSession, clip_id: str, names: set[str]
) -> bool:
    """One read: ``clip_id`` is still retired and no live clip references
    any of ``names`` (spellings of one file) as its video or thumbnail."""
    still_retired = exists().where(
        ClipRecord.id == clip_id, ClipRecord.retired.is_(True)
    )
    used_live = exists().where(
        ClipRecord.retired.is_(False),
        or_(ClipRecord.output_path.in_(names), ClipRecord.thumbnail_path.in_(names)),
    )
    return bool(await session.scalar(select(still_retired & ~used_live)))


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
