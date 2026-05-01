import asyncio

import pytest

from app.bus.event_bus import AsyncEventBus
from app.domain.events import Event, EventType


@pytest.mark.asyncio
async def test_publish_delivers_to_matching_subscriber():
    bus = AsyncEventBus()
    received: list[Event] = []

    async def consume():
        async for event in bus.subscribe(types=[EventType.VIDEO_REQUESTED]):
            received.append(event)
            if len(received) == 1:
                return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await bus.publish(Event(type=EventType.VIDEO_REQUESTED, job_id="j1"))
    await asyncio.wait_for(consumer, timeout=1.0)
    assert len(received) == 1
    assert received[0].type is EventType.VIDEO_REQUESTED


@pytest.mark.asyncio
async def test_two_subscribers_each_get_event():
    bus = AsyncEventBus()
    a, b = [], []

    async def make_consumer(target):
        async for event in bus.subscribe(types=[EventType.JOB_COMPLETED]):
            target.append(event)
            return

    ta = asyncio.create_task(make_consumer(a))
    tb = asyncio.create_task(make_consumer(b))
    await asyncio.sleep(0)
    await bus.publish(Event(type=EventType.JOB_COMPLETED, job_id="j1"))
    await asyncio.wait_for(asyncio.gather(ta, tb), timeout=1.0)
    assert len(a) == 1
    assert len(b) == 1


@pytest.mark.asyncio
async def test_unsubscribed_type_not_delivered():
    bus = AsyncEventBus()

    async def consume():
        async for event in bus.subscribe(types=[EventType.JOB_COMPLETED]):
            return event
        return None

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await bus.publish(Event(type=EventType.VIDEO_REQUESTED, job_id="j1"))
    await asyncio.sleep(0.05)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_job_id_filter_only_delivers_matching_job():
    bus = AsyncEventBus()
    received: list[Event] = []

    async def consume():
        async for event in bus.subscribe(job_id="j1"):
            received.append(event)
            if event.type is EventType.JOB_COMPLETED:
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await bus.publish(Event(type=EventType.VIDEO_REQUESTED, job_id="j2"))
    await bus.publish(Event(type=EventType.VIDEO_REQUESTED, job_id="j1"))
    await bus.publish(Event(type=EventType.JOB_COMPLETED, job_id="j1"))
    await asyncio.wait_for(task, timeout=1.0)
    assert [e.job_id for e in received] == ["j1", "j1"]
