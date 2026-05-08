from __future__ import annotations

import asyncio
from typing import Any, Callable, Protocol, runtime_checkable

from app.domain.models import ChapterArtifacts, JobState


class JobNotFoundError(KeyError):
    pass


@runtime_checkable
class JobStoreProtocol(Protocol):
    async def create(self, state: JobState) -> JobState: ...
    async def get(self, job_id: str) -> JobState: ...
    async def update(self, job_id: str, mutator: Callable[[JobState], None]) -> JobState: ...
    async def upsert_chapter(
        self, job_id: str, mutator: Callable[[ChapterArtifacts], None], chapter_index: int
    ) -> ChapterArtifacts: ...
    async def all_ids(self) -> list[str]: ...
    async def upsert_clip(
        self, job_id: str, clip_id: str, mutator: Callable[[dict[str, Any]], None]
    ) -> dict[str, Any]: ...
    async def list_jobs(
        self, limit: int = 20, offset: int = 0, search: str = ""
    ) -> list[JobState]: ...
    async def get_clip(self, clip_id: str) -> dict[str, Any] | None: ...
    async def list_clips(
        self,
        job_id: str | None = None,
        min_score: int | None = None,
        search: str = "",
    ) -> list[dict[str, Any]]: ...


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
        self, job_id: str, clip_id: str, mutator: Callable[[dict[str, Any]], None]
    ) -> dict[str, Any]:
        async with self._lock:
            clip = self._clips.get(clip_id, {"clip_id": clip_id, "job_id": job_id})
            mutator(clip)
            self._clips[clip_id] = clip
            return clip

    async def get_clip(self, clip_id: str) -> dict[str, Any] | None:
        async with self._lock:
            return self._clips.get(clip_id)

    async def list_jobs(
        self, limit: int = 20, offset: int = 0, search: str = ""
    ) -> list[JobState]:
        async with self._lock:
            jobs = list(self._jobs.values())
            if search:
                jobs = [j for j in jobs if search.lower() in j.url.lower()]
            return jobs[offset : offset + limit]

    async def list_clips(
        self,
        job_id: str | None = None,
        min_score: int | None = None,
        search: str = "",
    ) -> list[dict[str, Any]]:
        async with self._lock:
            clips = list(self._clips.values())
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


# Keep the old name as an alias so existing imports don't break during the transition.
JobStore = InMemoryJobStore


class SqlJobStore:
    """Postgres-backed store using SQLAlchemy async sessions."""

    def __init__(self) -> None:
        from app.db.session import get_session_factory
        self._factory = get_session_factory()

    async def create(self, state: JobState) -> JobState:
        from app.db.models import JobRecord
        from sqlalchemy import select

        async with self._factory() as session:
            record = JobRecord(
                id=state.job_id,
                youtube_url=state.url,
                source=state.source,
                status=state.status,
            )
            session.add(record)
            await session.commit()
        return state

    async def get(self, job_id: str) -> JobState:
        from app.db.models import JobRecord
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        async with self._factory() as session:
            result = await session.execute(
                select(JobRecord)
                .options(selectinload(JobRecord.clips))
                .where(JobRecord.id == job_id)
            )
            record = result.scalar_one_or_none()
        if record is None:
            raise JobNotFoundError(job_id)
        return _record_to_state(record)

    async def update(self, job_id: str, mutator: Callable[[JobState], None]) -> JobState:
        from app.db.models import JobRecord
        from sqlalchemy import select

        async with self._factory() as session:
            result = await session.execute(select(JobRecord).where(JobRecord.id == job_id))
            record = result.scalar_one_or_none()
            if record is None:
                raise JobNotFoundError(job_id)
            state = _record_to_state(record)
            mutator(state)
            record.status = state.status
            record.error = state.error
            record.source = state.source
            await session.commit()
        return state

    async def upsert_chapter(
        self,
        job_id: str,
        mutator: Callable[[ChapterArtifacts], None],
        chapter_index: int,
    ) -> ChapterArtifacts:
        from app.db.models import ChapterRecord, JobRecord
        from sqlalchemy import select

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
            chapter = ChapterArtifacts(chapter_index=chapter_index)
            mutator(chapter)
            record.title = chapter.clip_path or ""
            await session.commit()
        return chapter

    async def all_ids(self) -> list[str]:
        from app.db.models import JobRecord
        from sqlalchemy import select

        async with self._factory() as session:
            result = await session.execute(select(JobRecord.id))
            return [row[0] for row in result.all()]

    async def upsert_clip(
        self, job_id: str, clip_id: str, mutator: Callable[[dict[str, Any]], None]
    ) -> dict[str, Any]:
        from app.db.models import ClipRecord
        from sqlalchemy import select

        async with self._factory() as session:
            result = await session.execute(
                select(ClipRecord).where(ClipRecord.id == clip_id)
            )
            record = result.scalar_one_or_none()
            clip: dict[str, Any] = {"clip_id": clip_id, "job_id": job_id}
            if record:
                clip.update({
                    "start": record.start, "end": record.end,
                    "output_path": record.output_path,
                    "thumbnail_path": record.thumbnail_path,
                    "title": record.title, "summary": record.summary,
                    "hashtags": record.hashtags,
                    "virality_score": record.virality_score,
                    "score_breakdown": record.score_breakdown,
                    "transcript": record.transcript,
                    "liked": record.liked,
                    "disliked": record.disliked,
                })
            mutator(clip)
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

            if record is None:
                record = ClipRecord(id=clip_id, job_id=job_id)
                session.add(record)
            _apply_clip_to_record(record, clip)
            await session.commit()
        return clip

    async def get_clip(self, clip_id: str) -> dict[str, Any] | None:
        from app.db.models import ClipRecord
        from sqlalchemy import select

        async with self._factory() as session:
            result = await session.execute(
                select(ClipRecord).where(ClipRecord.id == clip_id)
            )
            record = result.scalar_one_or_none()
            return _clip_record_to_dict(record) if record else None

    async def list_jobs(
        self, limit: int = 20, offset: int = 0, search: str = ""
    ) -> list[JobState]:
        from app.db.models import JobRecord
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

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
            return [_record_to_state(r) for r in result.scalars().all()]

    async def list_clips(
        self,
        job_id: str | None = None,
        min_score: int | None = None,
        search: str = "",
    ) -> list[dict[str, Any]]:
        from app.db.models import ClipRecord
        from sqlalchemy import select

        async with self._factory() as session:
            q = select(ClipRecord).where(ClipRecord.retired == False)  # noqa: E712
            if job_id:
                q = q.where(ClipRecord.job_id == job_id)
            if min_score is not None:
                q = q.where(ClipRecord.virality_score >= min_score)
            if search:
                q = q.where(ClipRecord.title.ilike(f"%{search}%"))
            result = await session.execute(q)
            return [_clip_record_to_dict(r) for r in result.scalars().all()]


def _record_to_state(record: Any) -> JobState:
    loaded_clips = getattr(record, "clips", None) or []
    output_paths = [c.output_path for c in loaded_clips if c.output_path and not c.retired]
    return JobState(
        job_id=record.id,
        url=record.youtube_url,
        source=getattr(record, "source", None),
        status=record.status,
        error=record.error,
        download_path="/tmp/yt",
        output_paths=output_paths,
    )


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
    }
