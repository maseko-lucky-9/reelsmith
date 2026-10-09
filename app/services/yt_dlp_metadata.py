"""yt-dlp metadata lookups as a bounded async child process.

Runs ``python -m yt_dlp`` with the server's own interpreter, so the yt-dlp
pinned in ``requirements.txt`` is used rather than a ``yt-dlp`` CLI on
``PATH``. The lookup is a child process, not an in-process ``YoutubeDL`` in a
worker thread: a thread cannot be killed when a timeout fires, and it would
hold one of the few default-executor workers the lifespan configures. The
child is killed and reaped on timeout and on cancellation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from asyncio import create_subprocess_exec
from asyncio.subprocess import DEVNULL, PIPE
from collections.abc import Sequence
from typing import Any

log = logging.getLogger(__name__)

_STDERR_TAIL_CHARS = 500


async def run_bounded(
    argv: Sequence[str], *, timeout: float
) -> tuple[int, bytes, bytes]:
    """Run ``argv`` and return ``(returncode, stdout, stderr)``.

    Raises ``TimeoutError`` after ``timeout`` seconds. On timeout, cancellation
    or any other error while waiting, the child is killed and reaped first.
    """
    proc = await create_subprocess_exec(*argv, stdin=DEVNULL, stdout=PIPE, stderr=PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    return proc.returncode, stdout or b"", stderr or b""


async def dump_json(url: str, *, timeout: float) -> dict[str, Any]:
    """Metadata yt-dlp reports for ``url`` without downloading it.

    Returns ``{}`` when yt-dlp exits non-zero; raises ``TimeoutError`` when it
    does not finish within ``timeout`` seconds, and ``ValueError`` on output
    that is not JSON.
    """
    argv = [sys.executable, "-m", "yt_dlp", "--dump-json", "--no-playlist", "--", url]
    returncode, stdout, stderr = await run_bounded(argv, timeout=timeout)
    if returncode != 0:
        log.info(
            "yt-dlp metadata failed  rc=%s  url=%s  stderr=%s",
            returncode,
            url,
            stderr.decode("utf-8", errors="replace")[-_STDERR_TAIL_CHARS:].strip(),
        )
        return {}
    return json.loads(stdout)
