from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from app.domain.models import ChapterArtifacts, JobState, PipelineOptions

log = logging.getLogger(__name__)


class JobNotFoundError(KeyError):
    pass


# A clip mutator edits the clip dict in place; it may be sync or async.
ClipMutator = Callable[[dict[str, Any]], Awaitable[None] | None]


async def _apply_clip_mutator(mutator: ClipMutator, clip: dict[str, Any]) -> None:
    result = mutator(clip)
    if inspect.isawaitable(result):
        await result


# Statuses of a job that is queued or in flight; nothing survives a restart in
# these states (the queue and the pipelines live in the process). A
# "cancelled" job, like a completed or failed one, is final.
INTERRUPTIBLE_STATUSES = ("pending", "running")
INTERRUPTED_ERROR = "interrupted by restart"


# Clip fields that name a file the clip owns.
CLIP_FILE_KEYS = ("output_path", "thumbnail_path", "ai_hook_audio_path", "captions_burnt_path")


@dataclass
class PathReferences:
    """Every path the rows of the *other* jobs name, for a job delete (spec
    013): what that delete must never take. Only path columns are read."""

    # Each job's source (``video_path``); its words sidecar is derived.
    sources: list[str] = field(default_factory=list)
    # The file each ``upload://`` URL names (``video_path`` is set only at
    # the download step).
    upload_files: list[str] = field(default_factory=list)
    # The files of every clip, live or retired.
    clip_files: list[str] = field(default_factory=list)
    # Each job's ``destination_folder`` and ``clips_folder``.
    folders: list[str] = field(default_factory=list)


def _upload_file(url: str) -> str | None:
    if not url.startswith("upload://"):
        return None
    from urllib.parse import urlparse

    return urlparse(url).path or None


@runtime_checkable
class JobStoreProtocol(Protocol):
    async def create(self, state: JobState) -> JobState: ...
    async def get(self, job_id: str) -> JobState: ...
    async def update(self, job_id: str, mutator: Callable[[JobState], None]) -> JobState: ...
    async def increment_chapters_done(self, job_id: str) -> tuple[int, int]: ...
    async def upsert_chapter(
        self, job_id: str, mutator: Callable[[ChapterArtifacts], None], chapter_index: int
    ) -> ChapterArtifacts: ...
    async def all_ids(self) -> list[str]: ...
    async def upsert_clip(
        self, job_id: str, clip_id: str, mutator: ClipMutator
    ) -> dict[str, Any]: ...
    async def list_jobs(
        self, limit: int = 20, offset: int = 0, search: str = ""
    ) -> list[JobState]: ...
    async def find_job_by_url(
        self,
        url: str,
        statuses: Collection[str],
        where: Callable[[JobState], bool] | None = None,
    ) -> JobState | None: ...
    async def fail_interrupted_jobs(self) -> list[str]: ...
    async def get_clip(
        self, clip_id: str, *, include_retired: bool = False
    ) -> dict[str, Any] | None: ...
    async def list_clips(
        self,
        job_id: str | None = None,
        min_score: int | None = None,
        search: str = "",
        *,
        include_retired: bool = False,
    ) -> list[dict[str, Any]]: ...
    async def retire_clips(self, job_id: str, clip_ids: Sequence[str]) -> int: ...
    async def delete(self, job_id: str) -> bool: ...
    async def path_references(self, exclude_job_id: str) -> PathReferences: ...


class InMemoryJobStore:
    """Fast in-memory store used for unit tests and memory-mode operation."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobState] = {}
        self._clips: dict[str, dict[str, Any]] = {}  # clip_id → clip dict
        self._lock = asyncio.Lock()

    async def create(self, state: JobState) -> JobState:
        async with self._lock:
            self._jobs[state.job_id] = state
            return state

    async def get(self, job_id: str) -> JobState:
        async with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as e:
                raise JobNotFoundError(job_id) from e

    async def update(self, job_id: str, mutator: Callable[[JobState], None]) -> JobState:
        async with self._lock:
            try:
                state = self._jobs[job_id]
            except KeyError as e:
                raise JobNotFoundError(job_id) from e
            mutator(state)
            return state

    async def increment_chapters_done(self, job_id: str) -> tuple[int, int]:
        """Count one more finished chapter; return (done, total)."""
        async with self._lock:
            try:
                state = self._jobs[job_id]
            except KeyError as e:
                raise JobNotFoundError(job_id) from e
            state.chapters_done += 1
            return state.chapters_done, state.chapters_total

    async def upsert_chapter(
        self,
        job_id: str,
        mutator: Callable[[ChapterArtifacts], None],
        chapter_index: int,
    ) -> ChapterArtifacts:
        async with self._lock:
            try:
                state = self._jobs[job_id]
            except KeyError as e:
                raise JobNotFoundError(job_id) from e
            chapter = state.chapters.get(chapter_index)
            if chapter is None:
                chapter = ChapterArtifacts(chapter_index=chapter_index)
                state.chapters[chapter_index] = chapter
            mutator(chapter)
            return chapter

    async def all_ids(self) -> list[str]:
        async with self._lock:
            return list(self._jobs)

    async def upsert_clip(
        self, job_id: str, clip_id: str, mutator: ClipMutator
    ) -> dict[str, Any]:
        async with self._lock:
            clip = self._clips.get(clip_id, {"clip_id": clip_id, "job_id": job_id})
            await _apply_clip_mutator(mutator, clip)
            self._clips[clip_id] = clip
            return clip

    async def get_clip(
        self, clip_id: str, *, include_retired: bool = False
    ) -> dict[str, Any] | None:
        """The clip, or None if unknown or (unless asked for) retired."""
        async with self._lock:
            clip = self._clips.get(clip_id)
            if clip is None or (clip.get("retired") and not include_retired):
                return None
            return clip

    async def retire_clips(self, job_id: str, clip_ids: Sequence[str]) -> int:
        """Flag the live clips of ``job_id`` among ``clip_ids`` retired.

        Ids of other jobs, unknown ids and already-retired clips are left
        alone. Returns how many clips this call retired. Files are not
        deleted; that is the caller's job.
        """
        async with self._lock:
            retired = 0
            for clip_id in set(clip_ids):
                clip = self._clips.get(clip_id)
                if clip is None or clip.get("job_id") != job_id or clip.get("retired"):
                    continue
                clip["retired"] = True
                retired += 1
            return retired

    async def list_jobs(
        self, limit: int = 20, offset: int = 0, search: str = ""
    ) -> list[JobState]:
        async with self._lock:
            jobs = list(self._jobs.values())
            if search:
                jobs = [j for j in jobs if search.lower() in j.url.lower()]
            return jobs[offset : offset + limit]

    async def find_job_by_url(
        self,
        url: str,
        statuses: Collection[str],
        where: Callable[[JobState], bool] | None = None,
    ) -> JobState | None:
        """Newest job whose URL equals ``url`` exactly, whose status is in
        ``statuses`` and, given ``where``, for which ``where`` is true; None
        if there is none."""
        async with self._lock:
            return next(
                (
                    job
                    for job in reversed(self._jobs.values())
                    if job.url == url and job.status in statuses and (where is None or where(job))
                ),
                None,
            )

    async def fail_interrupted_jobs(self) -> list[str]:
        """Mark every pending/running job failed; return their ids.

        A fresh in-memory store is empty, so at startup this finds nothing;
        it exists so both stores honour the same protocol.
        """
        async with self._lock:
            interrupted = [
                job for job in self._jobs.values()
                if job.status in INTERRUPTIBLE_STATUSES
            ]
            for job in interrupted:
                job.status = "failed"
                job.error = INTERRUPTED_ERROR
            return [job.job_id for job in interrupted]

    async def list_clips(
        self,
        job_id: str | None = None,
        min_score: int | None = None,
        search: str = "",
        *,
        include_retired: bool = False,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            clips = [
                c for c in self._clips.values() if include_retired or not c.get("retired")
            ]
            if job_id:
                clips = [c for c in clips if c.get("job_id") == job_id]
            if min_score is not None:
                clips = [c for c in clips if (c.get("virality_score") or 0) >= min_score]
            if search:
                clips = [
                    c for c in clips
                    if search.lower() in (c.get("title") or "").lower()
                ]
            return clips

    async def delete(self, job_id: str) -> bool:
        """Forget the job and every clip of it, retired ones included; True if
        the job existed. Files are not touched (``retention.delete_job_files``)."""
        async with self._lock:
            existed = self._jobs.pop(job_id, None) is not None
            for clip_id in [i for i, c in self._clips.items() if c.get("job_id") == job_id]:
                del self._clips[clip_id]
            return existed

    async def path_references(self, exclude_job_id: str) -> PathReferences:
        """The paths every job but ``exclude_job_id`` names (``PathReferences``)."""
        refs = PathReferences()
        async with self._lock:
            for job in self._jobs.values():
                if job.job_id == exclude_job_id:
                    continue
                if job.video_path:
                    refs.sources.append(job.video_path)
                if upload := _upload_file(job.url):
                    refs.upload_files.append(upload)
                refs.clip_files += [p for p in job.output_paths if p]
                refs.folders += [f for f in (job.destination_folder, job.clips_folder) if f]
            for clip in self._clips.values():
                if clip.get("job_id") != exclude_job_id:
                    refs.clip_files += [clip[k] for k in CLIP_FILE_KEYS if clip.get(k)]
        return refs


# Keep the old name as an alias so existing imports don't break during the transition.
JobStore = InMemoryJobStore


# JobState fields that live in a column of ``jobs`` / ``chapters`` / ``clips``.
# Everything else (title, duration, current_step, the folders, the request's
# caption format and aspect ratio, ...) is stored in ``jobs.state``; deriving
# the set means a field added to JobState persists without touching the store.
_NOT_IN_STATE = frozenset({
    "job_id", "url", "source", "status", "error", "segment_mode", "language",
    "prompt", "auto_hook", "brand_template_id", "pipeline_options", "video_path",
    "chapters_total", "chapters_done", "chapters", "output_paths",
})
_STATE_FIELDS = frozenset(JobState.model_fields) - _NOT_IN_STATE
# Required JobState fields for a row that predates jobs.state (never stored).
_LEGACY_DEFAULTS: dict[str, Any] = {"download_path": "/tmp/yt"}


class SqlJobStore:
    """Postgres-backed store using SQLAlchemy async sessions."""

    def __init__(self) -> None:
        from app.db.session import get_session_factory
        self._factory = get_session_factory()

    async def create(self, state: JobState) -> JobState:

        from app.db.models import JobRecord

        async with self._factory() as session:
            record = JobRecord(
                id=state.job_id,
                youtube_url=state.url,
                source=state.source,
                status=state.status,
                segment_mode=state.segment_mode,
                language=state.language,
                prompt=state.prompt,
                auto_hook=state.auto_hook,
                brand_template_id=state.brand_template_id,
                pipeline_options=state.pipeline_options.model_dump(),
                video_path=state.video_path,
                chapters_total=state.chapters_total,
                state=state.model_dump(mode="json", include=_STATE_FIELDS),
            )
            session.add(record)
            await session.commit()
        return state

    async def get(self, job_id: str) -> JobState:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.db.models import JobRecord

        async with self._factory() as session:
            result = await session.execute(
                select(JobRecord)
                .options(selectinload(JobRecord.clips), selectinload(JobRecord.chapters))
                .where(JobRecord.id == job_id)
            )
            record = result.scalar_one_or_none()
        if record is None:
            raise JobNotFoundError(job_id)
        return _record_to_state(record)

    async def update(self, job_id: str, mutator: Callable[[JobState], None]) -> JobState:
        from sqlalchemy import delete, select
        from sqlalchemy.orm import selectinload

        from app.db.models import ChapterRecord, JobRecord

        async with self._factory() as session:
            result = await session.execute(
                select(JobRecord)
                .options(selectinload(JobRecord.clips), selectinload(JobRecord.chapters))
                .where(JobRecord.id == job_id)
            )
            record = result.scalar_one_or_none()
            if record is None:
                raise JobNotFoundError(job_id)
            state = _record_to_state(record)
            chapters_before = set(state.chapters)
            mutator(state)
            record.status = state.status
            record.error = state.error
            record.source = state.source
            record.segment_mode = state.segment_mode
            record.language = state.language
            record.prompt = state.prompt
            record.auto_hook = state.auto_hook
            record.brand_template_id = state.brand_template_id
            record.pipeline_options = state.pipeline_options.model_dump()
            record.video_path = state.video_path
            # Not chapters_done: this snapshot may predate a concurrent
            # increment_chapters_done, and writing it back would undo that.
            record.chapters_total = state.chapters_total
            record.state = state.model_dump(mode="json", include=_STATE_FIELDS)
            # Only pruning is honoured: reprompt drops replaced chapters through
            # here. Chapter artifacts are written by upsert_chapter alone, so a
            # stale snapshot here cannot undo a concurrent chapter write.
            pruned = chapters_before - set(state.chapters)
            if pruned:
                await session.execute(
                    delete(ChapterRecord)
                    .where(ChapterRecord.job_id == job_id)
                    .where(ChapterRecord.chapter_index.in_(pruned))
                )
            await session.commit()
        return state

    async def increment_chapters_done(self, job_id: str) -> tuple[int, int]:
        """Count one more finished chapter (one atomic UPDATE, so concurrent
        chapters lose no increment); return (done, total)."""
        from sqlalchemy import update

        from app.db.models import JobRecord

        async with self._factory() as session:
            result = await session.execute(
                update(JobRecord)
                .where(JobRecord.id == job_id)
                .values(chapters_done=JobRecord.chapters_done + 1)
                .returning(JobRecord.chapters_done, JobRecord.chapters_total)
            )
            row = result.one_or_none()
            await session.commit()
        if row is None:
            raise JobNotFoundError(job_id)
        return row[0], row[1]

    async def upsert_chapter(
        self,
        job_id: str,
        mutator: Callable[[ChapterArtifacts], None],
        chapter_index: int,
    ) -> ChapterArtifacts:
        from sqlalchemy import select

        from app.db.models import ChapterRecord

        async with self._factory() as session:
            result = await session.execute(
                select(ChapterRecord)
                .where(ChapterRecord.job_id == job_id)
                .where(ChapterRecord.chapter_index == chapter_index)
            )
            record = result.scalar_one_or_none()
            if record is None:
                record = ChapterRecord(job_id=job_id, chapter_index=chapter_index)
                session.add(record)
            chapter = _chapter_from_record(record) or ChapterArtifacts(chapter_index=chapter_index)
            mutator(chapter)
            record.artifacts = chapter.model_dump(mode="json")
            record.title = chapter.clip_path or ""
            await session.commit()
        return chapter

    async def all_ids(self) -> list[str]:
        from sqlalchemy import select

        from app.db.models import JobRecord

        async with self._factory() as session:
            result = await session.execute(select(JobRecord.id))
            return [row[0] for row in result.all()]

    async def upsert_clip(
        self, job_id: str, clip_id: str, mutator: ClipMutator
    ) -> dict[str, Any]:
        from sqlalchemy import select

        from app.db.models import ClipRecord

        async with self._factory() as session:
            result = await session.execute(
                select(ClipRecord).where(ClipRecord.id == clip_id)
            )
            record = result.scalar_one_or_none()
            # Reload through the same mapping the readers use, so a mutator
            # that sets one key does not wipe the columns it did not touch.
            clip: dict[str, Any] = (
                _clip_record_to_dict(record)
                if record
                else {"clip_id": clip_id, "job_id": job_id}
            )
            await _apply_clip_mutator(mutator, clip)
            def _apply_clip_to_record(r: Any, c: dict[str, Any]) -> None:
                r.start = c.get("start", 0.0)
                r.end = c.get("end", 0.0)
                r.output_path = c.get("output_path")
                r.thumbnail_path = c.get("thumbnail_path")
                r.title = c.get("title")
                r.summary = c.get("summary")
                r.hashtags = c.get("hashtags")
                r.virality_score = c.get("virality_score")
                r.score_breakdown = c.get("score_breakdown")
                r.transcript = c.get("transcript")
                r.liked = bool(c.get("liked", False))
                r.disliked = bool(c.get("disliked", False))
                r.retired = bool(c.get("retired", False))
                r.ai_hook_text = c.get("ai_hook_text")
                r.ai_hook_audio_path = c.get("ai_hook_audio_path")
                r.broll_assets = c.get("broll_assets")
                r.caption_style = c.get("caption_style") or "static"
                r.captions_burnt_path = c.get("captions_burnt_path")

            if record is None:
                record = ClipRecord(id=clip_id, job_id=job_id)
                session.add(record)
            _apply_clip_to_record(record, clip)
            await session.commit()
        return clip

    async def get_clip(
        self, clip_id: str, *, include_retired: bool = False
    ) -> dict[str, Any] | None:
        """The clip, or None if unknown or (unless asked for) retired —
        the same retired filter ``list_clips`` applies."""
        from sqlalchemy import select

        from app.db.models import ClipRecord

        async with self._factory() as session:
            q = select(ClipRecord).where(ClipRecord.id == clip_id)
            if not include_retired:
                q = q.where(ClipRecord.retired == False)  # noqa: E712
            result = await session.execute(q)
            record = result.scalar_one_or_none()
            return _clip_record_to_dict(record) if record else None

    async def retire_clips(self, job_id: str, clip_ids: Sequence[str]) -> int:
        """Flag the live clips of ``job_id`` among ``clip_ids`` retired (one
        UPDATE). Ids of other jobs, unknown ids and already-retired clips are
        left alone. Returns how many clips this call retired. Files are not
        deleted; that is the caller's job.
        """
        from sqlalchemy import update

        from app.db.models import ClipRecord

        ids = list(set(clip_ids))
        if not ids:
            return 0
        async with self._factory() as session:
            result = await session.execute(
                update(ClipRecord)
                .where(ClipRecord.job_id == job_id)
                .where(ClipRecord.id.in_(ids))
                .where(ClipRecord.retired.is_(False))
                .values(retired=True)
                .returning(ClipRecord.id)
            )
            retired = len(result.all())
            await session.commit()
        return retired

    async def list_jobs(
        self, limit: int = 20, offset: int = 0, search: str = ""
    ) -> list[JobState]:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.db.models import JobRecord

        async with self._factory() as session:
            q = (
                select(JobRecord)
                .options(selectinload(JobRecord.clips))
                .order_by(JobRecord.created_at.desc())
            )
            if search:
                q = q.where(JobRecord.youtube_url.ilike(f"%{search}%"))
            q = q.offset(offset).limit(limit)
            result = await session.execute(q)
            # No chapters: the home page and the badge poll this and never read them.
            return [_record_to_state(r, with_chapters=False) for r in result.scalars().all()]

    async def find_job_by_url(
        self,
        url: str,
        statuses: Collection[str],
        where: Callable[[JobState], bool] | None = None,
    ) -> JobState | None:
        """Newest job whose URL equals ``url`` exactly, whose status is in
        ``statuses`` and, given ``where``, for which ``where`` is true; None
        if there is none. One row without ``where``; with it, the jobs of
        that one URL, whatever the table size."""
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.db.models import JobRecord

        query = (
            select(JobRecord)
            .options(selectinload(JobRecord.clips))
            .where(JobRecord.youtube_url == url)
            .where(JobRecord.status.in_(list(statuses)))
            .order_by(JobRecord.created_at.desc())
        )
        async with self._factory() as session:
            if where is None:
                record = (await session.execute(query.limit(1))).scalar_one_or_none()
                return _record_to_state(record, with_chapters=False) if record is not None else None
            records = (await session.execute(query)).scalars().all()
        states = (_record_to_state(r, with_chapters=False) for r in records)
        return next((s for s in states if where(s)), None)

    async def fail_interrupted_jobs(self) -> list[str]:
        """Mark every pending/running job failed (one UPDATE); return their ids.

        Called once at startup: a previous process that stopped or crashed
        left these jobs without a pipeline, and they would otherwise block
        their URL in the duplicate check forever.
        """
        from sqlalchemy import update

        from app.db.models import JobRecord

        async with self._factory() as session:
            result = await session.execute(
                update(JobRecord)
                .where(JobRecord.status.in_(INTERRUPTIBLE_STATUSES))
                .values(status="failed", error=INTERRUPTED_ERROR)
                .returning(JobRecord.id)
            )
            failed_ids = [row[0] for row in result.all()]
            await session.commit()
        return failed_ids

    async def list_clips(
        self,
        job_id: str | None = None,
        min_score: int | None = None,
        search: str = "",
        *,
        include_retired: bool = False,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import func, select

        from app.db.models import ClipRecord

        async with self._factory() as session:
            q = select(ClipRecord)
            if not include_retired:
                q = q.where(ClipRecord.retired == False)  # noqa: E712
            if job_id:
                q = q.where(ClipRecord.job_id == job_id)
            if min_score is not None:
                # Match InMemoryJobStore: an unscored clip counts as 0.
                q = q.where(func.coalesce(ClipRecord.virality_score, 0) >= min_score)
            if search:
                q = q.where(ClipRecord.title.ilike(f"%{search}%"))
            result = await session.execute(q)
            return [_clip_record_to_dict(r) for r in result.scalars().all()]

    async def delete(self, job_id: str) -> bool:
        """Delete the job's row and every row that hangs off it, in one
        transaction; True if the job existed. Files are not touched
        (``retention.delete_job_files``).

        The child rows are deleted explicitly, deepest first, instead of
        relying on ``ON DELETE CASCADE``: SQLite enforces foreign keys only
        with ``PRAGMA foreign_keys=ON``, which the engine does not set.
        """
        from sqlalchemy import delete, select

        from app.db.models import (
            ChapterRecord,
            ClipAnalyticsSnapshot,
            ClipEdit,
            ClipRecord,
            JobRecord,
            PublishJob,
            ScheduledPost,
            ShareLink,
        )

        clip_ids = select(ClipRecord.id).where(ClipRecord.job_id == job_id)
        publish_ids = select(PublishJob.id).where(PublishJob.clip_id.in_(clip_ids))
        async with self._factory() as session:
            await session.execute(
                delete(ScheduledPost).where(ScheduledPost.publish_job_id.in_(publish_ids))
            )
            for table in (PublishJob, ClipEdit, ClipAnalyticsSnapshot, ShareLink):
                await session.execute(delete(table).where(table.clip_id.in_(clip_ids)))
            await session.execute(delete(ClipRecord).where(ClipRecord.job_id == job_id))
            await session.execute(delete(ChapterRecord).where(ChapterRecord.job_id == job_id))
            result = await session.execute(
                delete(JobRecord).where(JobRecord.id == job_id).returning(JobRecord.id)
            )
            existed = result.first() is not None
            await session.commit()
        return existed

    async def path_references(self, exclude_job_id: str) -> PathReferences:
        """The paths every job but ``exclude_job_id`` names
        (``PathReferences``): two queries that read path columns only (the
        folders come out of ``jobs.state`` by JSON key), not whole rows."""
        from sqlalchemy import select

        from app.db.models import ClipRecord, JobRecord

        refs = PathReferences()
        async with self._factory() as session:
            jobs = await session.execute(
                select(
                    JobRecord.video_path,
                    JobRecord.youtube_url,
                    JobRecord.state["destination_folder"].as_string(),
                    JobRecord.state["clips_folder"].as_string(),
                ).where(JobRecord.id != exclude_job_id)
            )
            for video_path, url, destination, clips_folder in jobs.all():
                if video_path:
                    refs.sources.append(video_path)
                if upload := _upload_file(url or ""):
                    refs.upload_files.append(upload)
                refs.folders += [f for f in (destination, clips_folder) if f]
            clips = await session.execute(
                select(*(getattr(ClipRecord, k) for k in CLIP_FILE_KEYS)).where(
                    ClipRecord.job_id != exclude_job_id
                )
            )
            refs.clip_files = [p for row in clips.all() for p in row if p]
        return refs


def _chapter_from_record(record: Any) -> ChapterArtifacts | None:
    """The chapter's stored artifacts; None for a row that has none (written
    before they were stored) or whose JSON no longer fits the model."""
    artifacts = getattr(record, "artifacts", None)
    if not isinstance(artifacts, dict):
        return None
    try:
        return ChapterArtifacts(**artifacts)
    except ValidationError:
        log.warning("Ignoring unreadable artifacts of chapter %s", record.id)
        return None


def _record_to_state(record: Any, *, with_chapters: bool = True) -> JobState:
    loaded_clips = getattr(record, "clips", None) or []
    output_paths = [c.output_path for c in loaded_clips if c.output_path and not c.retired]

    # Coerce None/missing/unreadable pipeline_options → defaults (G4: old rows must not crash)
    pipeline_opts = PipelineOptions()
    raw_opts = getattr(record, "pipeline_options", None)
    if raw_opts and isinstance(raw_opts, dict):
        try:
            pipeline_opts = PipelineOptions(**raw_opts)
        except ValidationError:
            log.warning("Ignoring unreadable pipeline_options of job %s", record.id)

    columns: dict[str, Any] = {
        "job_id": record.id,
        "url": record.youtube_url,
        "source": getattr(record, "source", None),
        "status": record.status,
        "error": record.error,
        "output_paths": output_paths,
        "segment_mode": getattr(record, "segment_mode", None) or "auto",
        "language": getattr(record, "language", None) or "en-US",
        "prompt": getattr(record, "prompt", None),
        "auto_hook": getattr(record, "auto_hook", None) if getattr(record, "auto_hook", None) is not None else True,
        "brand_template_id": getattr(record, "brand_template_id", None),
        "pipeline_options": pipeline_opts,
        "video_path": getattr(record, "video_path", None),
        "chapters_total": getattr(record, "chapters_total", None) or 0,
        "chapters_done": getattr(record, "chapters_done", None) or 0,
    }
    stored = getattr(record, "state", None)
    if not isinstance(stored, dict):
        stored = {}
    chapters = {}
    if with_chapters:
        chapters = {
            c.chapter_index: artifacts
            for c in getattr(record, "chapters", None) or []
            if (artifacts := _chapter_from_record(c)) is not None
        }
    # Columns last: they are the source of truth for the fields they hold.
    data = {**_LEGACY_DEFAULTS, **stored, **columns, "chapters": chapters}
    try:
        return JobState(**data)
    except ValidationError as exc:
        # One unreadable stored field must not fail a whole listing, nor cost
        # the job its other fields (the next update() would save the loss).
        bad = {e["loc"][0] for e in exc.errors() if e["loc"]} & (set(stored) - set(columns))
        log.warning("Dropping unreadable stored field(s) %s of job %s", sorted(bad), record.id)
        return JobState(**{k: v for k, v in data.items() if k not in bad})


def _clip_record_to_dict(record: Any) -> dict[str, Any]:
    return {
        "clip_id": record.id,
        "job_id": record.job_id,
        "chapter_id": record.chapter_id,
        "start": record.start,
        "end": record.end,
        "output_path": record.output_path,
        "thumbnail_path": record.thumbnail_path,
        "title": record.title,
        "summary": record.summary,
        "hashtags": record.hashtags,
        "virality_score": record.virality_score,
        "score_breakdown": record.score_breakdown,
        "transcript": record.transcript,
        "liked": record.liked,
        "disliked": record.disliked,
        "retired": record.retired,
        "ai_hook_text": record.ai_hook_text,
        "ai_hook_audio_path": record.ai_hook_audio_path,
        "broll_assets": record.broll_assets,
        "caption_style": record.caption_style,
        "captions_burnt_path": record.captions_burnt_path,
    }
