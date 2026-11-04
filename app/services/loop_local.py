"""A semaphore that belongs to the running event loop.

A module-level ``asyncio.Semaphore`` binds to the first loop that has to wait
on it and then fails on any other loop (every test gets its own). One server
runs one loop, so this changes nothing there; it keeps module-level limits
usable from tests and from a second loop.
"""

from __future__ import annotations

import asyncio
import weakref


class LoopLocalSemaphore:
    def __init__(self, value: int) -> None:
        self._value = value
        self._by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
            weakref.WeakKeyDictionary()
        )

    def get(self) -> asyncio.Semaphore:
        """The semaphore of the running loop."""
        loop = asyncio.get_running_loop()
        semaphore = self._by_loop.get(loop)
        if semaphore is None:
            semaphore = self._by_loop[loop] = asyncio.Semaphore(self._value)
        return semaphore
