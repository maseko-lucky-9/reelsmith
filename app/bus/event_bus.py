from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Iterable

from app.domain.events import Event, EventType

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)


class _Subscription:
    __slots__ = ("queue", "types", "job_id")

    def __init__(
        self,
        queue: asyncio.Queue[Event | None],
        types: tuple[EventType, ...] | None,
        job_id: str | None,
    ) -> None:
        self.queue = queue
        self.types = types
        self.job_id = job_id

    def matches(self, event: Event) -> bool:
        if self.types is not None and event.type not in self.types:
            return False
        if self.job_id is not None and event.job_id != self.job_id:
            return False
        return True


class AsyncEventBus:
    """In-process pub/sub. Subscribers receive every matching event published
    after the subscription is created. Closed via ``aclose()``.
    """

    def __init__(self) -> None:
        self._subscriptions: list[_Subscription] = []
        self._lock = asyncio.Lock()
        self._closed = False

    async def publish(self, event: Event) -> None:
        if self._closed:
            return
        async with self._lock:
            subs = list(self._subscriptions)
        matched = [s for s in subs if s.matches(event)]
        log.debug("Event published  type=%s  job_id=%s  subscribers=%d",
                  event.type.value, event.job_id, len(matched))
        for sub in matched:
            await sub.queue.put(event)

    async def subscribe(
        self,
        types: Iterable[EventType] | None = None,
        job_id: str | None = None,
    ) -> AsyncIterator[Event]:
        type_tuple: tuple[EventType, ...] | None = tuple(types) if types else None
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        sub = _Subscription(queue=queue, types=type_tuple, job_id=job_id)
        async with self._lock:
            self._subscriptions.append(sub)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            async with self._lock:
                if sub in self._subscriptions:
                    self._subscriptions.remove(sub)

    async def aclose(self) -> None:
        self._closed = True
        async with self._lock:
            subs = list(self._subscriptions)
            self._subscriptions.clear()
        for sub in subs:
            await sub.queue.put(None)
