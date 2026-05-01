from __future__ import annotations

import asyncio
from typing import Callable

from app.domain.models import ChapterArtifacts, JobState


class JobNotFoundError(KeyError):
    pass


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, JobState] = {}
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
