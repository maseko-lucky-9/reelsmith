"""Killable child processes for the pipeline: stop politely, then for good.

``run_streaming`` runs a child whose stdout is a stream of lines (progress
reports) and hands each line to a callback as it arrives. On timeout,
cancellation or any other error it stops the child with ``stop``: SIGTERM
first, so a child that cleans up on SIGTERM (``scripts/scene_index.py``
removes its temp chunk directory and kills its ffmpeg) can do so, then
SIGKILL to the child's whole process group after ``terminate_grace``
seconds. The child runs in a new session, so a grandchild it started (an
ffmpeg) is in that group; the group is killed whenever the child is
stopped, also when the child itself already exited.

Output is read in chunks, not with ``StreamReader.readline``: a child that
writes a long line with no newline (a ``\\r`` progress bar of a model
download) must not raise ``LimitOverrunError`` in the parent.
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import inspect
import logging
import os
import re
import signal
from asyncio import StreamReader
from asyncio.subprocess import DEVNULL, PIPE, Process
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence

log = logging.getLogger(__name__)

DEFAULT_TERMINATE_GRACE = 5.0
"""Seconds a child gets between SIGTERM and SIGKILL."""

STDERR_TAIL_LINES = 20
"""Last stderr lines kept for an error message."""

READ_CHUNK_BYTES = 64 * 1024
MAX_LINE_CHARS = 1024 * 1024
"""A partial line longer than this is dropped (logged) instead of buffered."""

LineHandler = Callable[[str], Awaitable[None] | None]


async def stop(proc: Process, grace: float) -> None:
    """Stop ``proc`` (a process-group leader) and reap it.

    SIGTERM, wait ``grace`` s (none when ``grace <= 0``), then SIGKILL to the
    whole process group, so a grandchild that outlived its parent dies too.
    A child that already exited only has its group killed.
    """
    if proc.returncode is None and grace > 0:
        with contextlib.suppress(ProcessLookupError):
            proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), grace)
        except TimeoutError:
            log.warning("child pid=%s ignored SIGTERM for %.1fs; killing it", proc.pid, grace)
    _kill_group(proc)
    if proc.returncode is None:
        await proc.wait()


def _kill_group(proc: Process) -> None:
    # The child leads its own session (start_new_session), so its pid is the
    # group id; the group outlives the leader while a member is alive.
    # Accepted risk (spec 014): once the leader is reaped and the group is
    # empty, the id could in theory be reused by a new session leader before
    # this runs; run_streaming skips the kill when both pipes reached EOF.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    if proc.returncode is None:
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


async def _lines(stream: StreamReader, separators: str) -> AsyncIterator[str]:
    """Lines of ``stream`` split on any of ``separators``, read in chunks."""
    split = re.compile(f"[{re.escape(separators)}]")
    # Incremental: a multi-byte character split across two reads stays whole.
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = ""
    while chunk := await stream.read(READ_CHUNK_BYTES):
        *complete, pending = split.split(pending + decoder.decode(chunk))
        for line in complete:
            yield line
        if len(pending) > MAX_LINE_CHARS:
            log.warning("child output line over %d characters dropped", MAX_LINE_CHARS)
            pending = ""
    pending += decoder.decode(b"", final=True)
    if pending:
        yield pending


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
    ``STDERR_TAIL_LINES`` lines of stderr (``\\r`` also ends a line there).
    Raises ``TimeoutError`` after ``timeout`` seconds. On timeout,
    cancellation, or an error raised by ``on_line``, the child and its
    process group are stopped (``stop``) and reaped before the error
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
        async for raw in _lines(proc.stdout, "\n"):
            line = raw.strip()
            if not line:
                continue
            result = on_line(line)
            if inspect.isawaitable(result):
                await result

    async def _pump_stderr() -> None:
        assert proc.stderr is not None
        async for raw in _lines(proc.stderr, "\r\n"):
            line = raw.rstrip()
            if line:
                tail.append(line)

    pumps = [asyncio.ensure_future(_pump_stdout()), asyncio.ensure_future(_pump_stderr())]
    try:
        async with asyncio.timeout(timeout):
            await asyncio.gather(*pumps)
            returncode = await proc.wait()
    except BaseException:
        # Both pipes at EOF and the child reaped: nothing of the group can
        # still hold them, so no kill is sent (a killpg to a pid that may be
        # reused). Otherwise a grandchild may live on: stop the group.
        drained = all(p.done() and not p.cancelled() and p.exception() is None for p in pumps)
        # gather leaves the other pump running when one fails.
        for pump in pumps:
            pump.cancel()
        if not (drained and proc.returncode is not None) and await stop_uninterrupted(proc, terminate_grace):
            raise asyncio.CancelledError from None
        raise
    return returncode, "\n".join(tail)
