"""Killable child processes for the pipeline: stop politely, then for good.

``run_streaming`` runs a child whose stdout is a stream of lines (progress
reports) and hands each line to a callback as it arrives. On timeout,
cancellation or any other error it stops the child with ``stop``: SIGTERM
first, so a child that cleans up on SIGTERM (``scripts/scene_index.py``
removes its temp chunk directory and kills its ffmpeg) can do so, then
SIGKILL to the child's whole process group after ``terminate_grace``
seconds. The child runs in a new session, so a grandchild it started (an
ffmpeg) is in that group and dies with it.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import signal
from asyncio.subprocess import DEVNULL, PIPE, Process
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence

log = logging.getLogger(__name__)

DEFAULT_TERMINATE_GRACE = 5.0
"""Seconds a child gets between SIGTERM and SIGKILL."""

STDERR_TAIL_LINES = 20
"""Last stderr lines kept for an error message."""

LineHandler = Callable[[str], Awaitable[None] | None]


async def stop(proc: Process, grace: float) -> None:
    """Stop ``proc`` and reap it: SIGTERM, wait ``grace`` s, then SIGKILL.

    With ``grace <= 0`` it is killed straight away. The SIGKILL goes to the
    child's process group when it leads one (``start_new_session``), else to
    the child alone. A child that already exited is only reaped.
    """
    if proc.returncode is not None:
        return
    if grace > 0:
        with contextlib.suppress(ProcessLookupError):
            proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), grace)
            return
        except TimeoutError:
            log.warning("child pid=%s ignored SIGTERM for %.1fs; killing it", proc.pid, grace)
    _kill_group(proc)
    await proc.wait()


def _kill_group(proc: Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if os.getpgid(proc.pid) == proc.pid:
            os.killpg(proc.pid, signal.SIGKILL)
            return
    with contextlib.suppress(ProcessLookupError):
        proc.kill()


async def stop_uninterrupted(proc: Process, grace: float) -> bool:
    """``stop`` that a further cancel of the caller cannot cut short.

    Returns True when the caller was cancelled while it waited; the caller
    then re-raises ``CancelledError`` once it has cleaned up.
    """
    task = asyncio.ensure_future(stop(proc, grace))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    return cancelled


async def run_streaming(
    argv: Sequence[str],
    *,
    timeout: float,
    on_line: LineHandler,
    terminate_grace: float = DEFAULT_TERMINATE_GRACE,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str]:
    """Run ``argv``; call ``on_line`` with each non-empty stdout line.

    Returns ``(returncode, stderr_tail)``: the exit status and the last
    ``STDERR_TAIL_LINES`` lines of stderr. Raises ``TimeoutError`` after
    ``timeout`` seconds. On timeout, cancellation, or an error raised by
    ``on_line``, the child is stopped (``stop``) and reaped before the error
    propagates.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=DEVNULL,
        stdout=PIPE,
        stderr=PIPE,
        env=dict(env) if env is not None else None,
        start_new_session=True,
    )
    tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)

    async def _pump_stdout() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            result = on_line(line)
            if inspect.isawaitable(result):
                await result

    async def _pump_stderr() -> None:
        assert proc.stderr is not None
        async for raw in proc.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                tail.append(line)

    pumps = [asyncio.ensure_future(_pump_stdout()), asyncio.ensure_future(_pump_stderr())]
    try:
        async with asyncio.timeout(timeout):
            await asyncio.gather(*pumps)
            returncode = await proc.wait()
    except BaseException:
        # gather leaves the other pump running when one fails.
        for pump in pumps:
            pump.cancel()
        if proc.returncode is None and await stop_uninterrupted(proc, terminate_grace):
            raise asyncio.CancelledError from None
        raise
    return returncode, "\n".join(tail)
