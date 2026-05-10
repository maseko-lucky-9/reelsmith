"""SSE heartbeat helper (W2.10).

Wraps an async event iterator with periodic ': ping\\n\\n' comment
frames so proxies / load balancers don't drop idle long-stage
connections (Coqui XTTS, demucs, animated-caption render).

Usage::

    from app.sse_heartbeat import with_heartbeat

    async def stream():
        async for evt in with_heartbeat(real_events()):
            yield evt
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterable, AsyncIterator

from app.settings import settings

log = logging.getLogger(__name__)

KEEPALIVE_FRAME = ": ping\n\n"


async def with_heartbeat(
    source: AsyncIterable[str],
    *,
    interval_seconds: float | None = None,
) -> AsyncIterator[str]:
    """Yield from ``source``, interleaving heartbeat comment frames every
    ``interval_seconds``. Heartbeat is disabled when interval <= 0."""
    interval = interval_seconds if interval_seconds is not None else getattr(
        settings, "sse_keepalive_seconds", 15
    )
    if interval <= 0:
        async for evt in source:
            yield evt
        return

    iterator = aiter(source)

    async def _next() -> str | None:
        try:
            return await iterator.__anext__()
        except StopAsyncIteration:
            return None

    pending = asyncio.create_task(_next())
    try:
        while True:
            try:
                evt = await asyncio.wait_for(asyncio.shield(pending), timeout=interval)
            except asyncio.TimeoutError:
                yield KEEPALIVE_FRAME
                continue
            if evt is None:
                return
            yield evt
            pending = asyncio.create_task(_next())
    finally:
        if not pending.done():
            pending.cancel()
            try:
                await pending
            except (asyncio.CancelledError, BaseException):
                pass
