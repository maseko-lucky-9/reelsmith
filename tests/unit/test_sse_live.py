"""``GET /jobs/{id}/events?live=true``: no history replay, and a terminal event
does not end the stream (the clip editor watches a completed job for a
re-render's ClipRendered / ClipRerenderFailed)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.bus.event_bus import AsyncEventBus
from app.domain.events import Event, EventType
from app.routers import jobs as jobs_router


class _Store:
    async def get(self, job_id: str) -> object:
        return object()


async def _collect(live: bool, publish_after: list[Event], count: int) -> list[str]:
    bus = AsyncEventBus()
    await bus.publish(Event(type=EventType.JOB_COMPLETED, job_id="j1"))
    state = SimpleNamespace(event_bus=bus, job_store=_Store())
    response = await jobs_router.stream_job_events("j1", SimpleNamespace(app=SimpleNamespace(state=state)), live=live)
    got: list[str] = []

    async def consume() -> None:
        async for frame in response.body_iterator:
            if isinstance(frame, dict):
                got.append(frame["event"])
            if len(got) >= count:
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    for event in publish_after:
        await bus.publish(event)
    try:
        await asyncio.wait_for(task, timeout=0.5)
    except asyncio.TimeoutError:
        task.cancel()
    return got


async def test_default_stream_replays_and_ends_at_the_terminal_event() -> None:
    later = Event(type=EventType.CLIP_RENDERED, job_id="j1", payload={"clip_id": "c1"})
    assert await _collect(False, [later], 2) == ["JobCompleted"]


async def test_live_stream_skips_replay_and_survives_a_terminal_event() -> None:
    events = [
        Event(type=EventType.JOB_COMPLETED, job_id="j1"),
        Event(type=EventType.CLIP_RENDERED, job_id="j1", payload={"clip_id": "c1"}),
    ]
    assert await _collect(True, events, 2) == ["JobCompleted", "ClipRendered"]
