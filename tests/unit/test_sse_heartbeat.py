"""Unit tests for sse_heartbeat helper (W2.10)."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from app.sse_heartbeat import KEEPALIVE_FRAME, with_heartbeat


async def _gen(items: list[str], delay: float = 0.0) -> AsyncIterator[str]:
    for it in items:
        if delay > 0:
            await asyncio.sleep(delay)
        yield it


async def test_passthrough_when_disabled():
    out = []
    async for evt in with_heartbeat(_gen(["a", "b", "c"]), interval_seconds=0):
        out.append(evt)
    assert out == ["a", "b", "c"]


async def test_emits_heartbeat_when_idle():
    async def slow():
        await asyncio.sleep(0.15)
        yield "first"
        await asyncio.sleep(0.15)
        yield "second"

    out = []
    async for evt in with_heartbeat(slow(), interval_seconds=0.05):
        out.append(evt)
        if len(out) >= 6:
            break

    # Most of the early emissions are heartbeats since events are slow.
    pings = [e for e in out if e == KEEPALIVE_FRAME]
    real = [e for e in out if e != KEEPALIVE_FRAME]
    assert len(pings) >= 1
    assert "first" in real or "second" in real


async def test_stops_when_source_exhausts():
    out = []
    async for evt in with_heartbeat(_gen(["x"]), interval_seconds=10):
        out.append(evt)
    assert out == ["x"]
