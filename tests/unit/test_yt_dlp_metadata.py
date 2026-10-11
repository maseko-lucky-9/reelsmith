"""Metadata lookups run yt-dlp as a bounded async child process (T003 / former E4).

``python -m yt_dlp`` of the running interpreter replaces the ``yt-dlp`` CLI on
``PATH``; the child is killed on timeout and on cancellation.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest

from app.services import yt_dlp_metadata
from app.services.yt_dlp_metadata import dump_json, run_bounded

URL = "https://www.youtube.com/watch?v=abc"


class FakeProc:
    def __init__(self, stdout: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._final_rc = returncode
        self.returncode: int | None = None

    async def communicate(self, input=None):
        self.returncode = self._final_rc
        return self._stdout, b""

    def kill(self) -> None:  # pragma: no cover - not reached on the happy path
        self.returncode = -9

    async def wait(self) -> int:  # pragma: no cover
        return self.returncode


def _install(monkeypatch, proc):
    calls = []

    async def fake_exec(*argv, **kwargs):
        calls.append((argv, kwargs))
        return proc

    monkeypatch.setattr(yt_dlp_metadata, "create_subprocess_exec", fake_exec)
    return calls


async def test_dump_json_runs_the_yt_dlp_module_of_this_interpreter(monkeypatch):
    calls = _install(monkeypatch, FakeProc(json.dumps({"title": "T"}).encode()))

    info = await dump_json(URL, timeout=5)

    assert info == {"title": "T"}
    argv, _ = calls[0]
    assert argv[:3] == (sys.executable, "-m", "yt_dlp")
    assert "--dump-json" in argv and "--no-playlist" in argv
    # The URL is a positional after "--" so it can never be read as an option.
    assert argv[-2:] == ("--", URL)


async def test_dump_json_non_zero_exit_returns_empty(monkeypatch):
    _install(monkeypatch, FakeProc(b"", returncode=1))

    assert await dump_json(URL, timeout=5) == {}


def _spy_on_spawn(monkeypatch):
    procs = []
    real = yt_dlp_metadata.create_subprocess_exec

    async def spy(*argv, **kwargs):
        proc = await real(*argv, **kwargs)
        procs.append(proc)
        return proc

    monkeypatch.setattr(yt_dlp_metadata, "create_subprocess_exec", spy)
    return procs


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


_SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)"]


async def test_run_bounded_kills_a_hung_child_at_the_timeout(monkeypatch):
    procs = _spy_on_spawn(monkeypatch)

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(run_bounded(_SLEEPER, timeout=0.5), timeout=15)
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"timeout not enforced ({elapsed:.1f}s)"
    (proc,) = procs
    assert proc.returncode is not None, "child was not killed and reaped"
    assert _gone(proc.pid)


async def test_run_bounded_kills_the_child_when_cancelled(monkeypatch):
    procs = _spy_on_spawn(monkeypatch)

    task = asyncio.create_task(run_bounded(_SLEEPER, timeout=30))
    for _ in range(100):
        if procs:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.1)
    task.cancel()
    cancelled_at = time.monotonic()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=15)
    elapsed = time.monotonic() - cancelled_at

    assert elapsed < 5, f"cancel waited for the child to exit ({elapsed:.1f}s)"

    (proc,) = procs
    assert proc.returncode is not None, "child was not killed on cancel"
    assert _gone(proc.pid)
