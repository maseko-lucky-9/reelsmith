"""Thin, CI-portable wrappers around the bundled ffmpeg binary and PyAV.

* ``exe()`` — the ffmpeg shipped by ``imageio-ffmpeg`` (has libx264, the
  concat demuxer and the overlay/loop filters on every platform it publishes
  wheels for). Never a system ffmpeg: Homebrew builds differ in filters.
* ``run()`` — runs one ffmpeg (or any) argv and guarantees the child is gone
  when it returns: it is killed on timeout, on a cooperative cancel signal and
  on any ``BaseException`` (``KeyboardInterrupt``, ``SystemExit``...). Failures
  raise ``FfmpegError`` carrying the stderr tail.
* ``to_thread_cancellable()`` — ``asyncio.to_thread`` whose cancellation (e.g.
  an ``asyncio.wait_for`` timeout) reaches every ``run()`` inside the worker
  thread and kills its ffmpeg instead of orphaning it.
* ``duration()`` / ``fps()`` / ``grab_frame()`` — PyAV probes; no ffprobe
  (imageio-ffmpeg does not ship one).

VFR rule: ``fps()`` returns the stream's *average* frame rate. Renders are
written at that constant rate (``-r <average_rate>``), so variable-frame-rate
sources come out CFR. MoviePy used the container's nominal rate instead (e.g.
120 fps for a phone VFR clip); the average rate is the deliberate replacement.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Literal, TypeVar

import av
import imageio_ffmpeg
from PIL import Image

log = logging.getLogger(__name__)

T = TypeVar("T")

# How often a running ffmpeg is checked for a cancel request.
_POLL_SECONDS = 0.1
_STDERR_TAIL_CHARS = 2000
_MESSAGE_TAIL_CHARS = 300

_cancel_event: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "ffmpeg_tools_cancel_event", default=None
)


class FfmpegError(RuntimeError):
    """An ffmpeg run failed, timed out or was cancelled."""

    def __init__(self, message: str, *, returncode: int | None, stderr_tail: str):
        super().__init__(message)
        self.returncode = returncode
        self.stderr_tail = stderr_tail


class FfmpegTimeout(FfmpegError):
    """The process exceeded its timeout and was killed."""


class FfmpegCancelled(FfmpegError):
    """The process was killed because its cancel event was set."""


@functools.cache
def exe() -> str:
    """Absolute path of the bundled ffmpeg binary."""
    return imageio_ffmpeg.get_ffmpeg_exe()


def resolve_argv(argv: Sequence[str]) -> list[str]:
    """Replace a bare ``ffmpeg`` program name with the bundled binary."""
    resolved = [str(a) for a in argv]
    if resolved and resolved[0] == "ffmpeg":
        resolved[0] = exe()
    return resolved


def _tail(data: bytes | None) -> str:
    return (data or b"").decode("utf-8", errors="replace")[-_STDERR_TAIL_CHARS:]


def _short(stderr: str) -> str:
    """Last ~300 chars of stderr for exception messages (they reach
    JobFailed.error / the UI); the full tail stays on ``stderr_tail`` and in
    the log."""
    text = stderr.strip()
    return (
        text if len(text) <= _MESSAGE_TAIL_CHARS else "…" + text[-_MESSAGE_TAIL_CHARS:]
    )


def _kill(proc: subprocess.Popen) -> bytes:
    """Kill ``proc`` (if still running), reap it and return what stderr held."""
    if proc.poll() is None:
        proc.kill()
    try:
        _out, err = proc.communicate(timeout=5)
    except Exception:  # noqa: BLE001 — best effort; the process is already killed
        proc.wait()
        err = b""
    return err or b""


def run(
    argv: Sequence[str],
    *,
    timeout: float | None = None,
    cancel: threading.Event | None = None,
    cwd: str | Path | None = None,
) -> str:
    """Run ``argv`` to completion and return its stderr text.

    ``argv[0] == "ffmpeg"`` is resolved to the bundled binary; any other
    program runs as given. The child is killed and reaped before this returns
    or raises, whatever happens: non-zero exit (``FfmpegError``), ``timeout``
    seconds elapsed (``FfmpegTimeout``), ``cancel`` set (``FfmpegCancelled``;
    defaults to the event installed by ``to_thread_cancellable``) or any
    ``BaseException`` raised while waiting (re-raised after the kill).
    """
    resolved = resolve_argv(argv)
    cancel = cancel if cancel is not None else _cancel_event.get()
    if cancel is not None and cancel.is_set():
        raise FfmpegCancelled(
            f"{Path(resolved[0]).name} not started: cancelled",
            returncode=None,
            stderr_tail="",
        )
    log.debug("ffmpeg_tools.run: %s", " ".join(resolved))
    proc = subprocess.Popen(
        resolved,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        cwd=str(cwd) if cwd is not None else None,
    )
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        while True:
            wait = _POLL_SECONDS if cancel is not None else None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    err = _kill(proc)
                    raise FfmpegTimeout(
                        f"{Path(resolved[0]).name} timed out after {timeout:.1f}s "
                        f"and was killed: {_short(_tail(err))}",
                        returncode=proc.returncode,
                        stderr_tail=_tail(err),
                    )
                wait = remaining if wait is None else min(wait, remaining)
            try:
                _out, err = proc.communicate(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    err = _kill(proc)
                    raise FfmpegCancelled(
                        f"{Path(resolved[0]).name} cancelled and killed",
                        returncode=proc.returncode,
                        stderr_tail=_tail(err),
                    ) from None
    except BaseException:
        _kill(proc)
        raise
    stderr = _tail(err)
    if proc.returncode != 0:
        log.warning(
            "%s failed (rc=%s); stderr tail:\n%s",
            Path(resolved[0]).name,
            proc.returncode,
            stderr,
        )
        raise FfmpegError(
            f"{Path(resolved[0]).name} failed (rc={proc.returncode}): {_short(stderr)}",
            returncode=proc.returncode,
            stderr_tail=stderr,
        )
    return stderr


def current_cancel_event() -> threading.Event | None:
    """The cancel event installed by ``to_thread_cancellable`` for this thread."""
    return _cancel_event.get()


async def to_thread_cancellable(func: Callable[..., T], /, *args, **kwargs) -> T:
    """``asyncio.to_thread`` whose cancellation kills ffmpeg runs inside ``func``.

    Installs a fresh cancel event for the worker thread (via a context
    variable that ``run()`` reads). If the awaiting task is cancelled, the
    event is set and ``CancelledError`` propagates:

    * a call still queued behind a busy executor is dropped — it will never
      run, and cancellation does not wait for it;
    * a running call is awaited until its ffmpeg has been killed and reaped.
    """
    event = threading.Event()
    ctx = contextvars.copy_context()
    ctx.run(_cancel_event.set, event)
    lock = threading.Lock()
    state = {"started": False, "dropped": False}

    def worker():
        with lock:
            if state["dropped"]:
                return None
            state["started"] = True
        return ctx.run(func, *args, **kwargs)

    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(None, worker)
    try:
        # asyncio.wait never cancels what it waits on, so a cancel stops only
        # this coroutine. (Not asyncio.shield: on Python 3.14 it logs the
        # worker's post-cancel exception as "exception in shielded future".)
        await asyncio.wait({future})
    except asyncio.CancelledError:
        event.set()
        with lock:
            if not state["started"]:
                state["dropped"] = True
        if not state["dropped"]:
            try:
                await asyncio.wait({future})
            finally:
                if future.done() and not future.cancelled():
                    future.exception()  # mark retrieved; the cancel wins
        raise
    return future.result()


# ── PyAV probes ───────────────────────────────────────────────────────────────

DurationKind = Literal["container", "video", "audio"]


def duration(path: str | Path, kind: DurationKind = "container") -> float | None:
    """Duration in seconds via PyAV.

    ``container`` — the demuxer's overall duration (what ffmpeg prints).
    ``video`` / ``audio`` — the first stream of that type, falling back to the
    container duration when the stream does not declare one (e.g. mkv/webm).
    Returns ``None`` when the requested stream type is absent.
    """
    with av.open(str(path)) as container:
        overall = (
            container.duration / av.time_base
            if container.duration is not None
            else None
        )
        if kind == "container":
            if overall is None:
                raise ValueError(f"{path}: container declares no duration")
            return float(overall)
        streams = (
            container.streams.video if kind == "video" else container.streams.audio
        )
        if not streams:
            return None
        stream = streams[0]
        if stream.duration is not None and stream.time_base is not None:
            return float(stream.duration * stream.time_base)
        if overall is None:
            raise ValueError(f"{path}: {kind} stream declares no duration")
        return float(overall)


def has_audio(path: str | Path) -> bool:
    with av.open(str(path)) as container:
        return bool(container.streams.audio)


def _displayed(frame: av.VideoFrame) -> Image.Image:
    """RGB image of ``frame`` as players (and the ffmpeg CLI) show it.

    Phone footage often carries a display-matrix rotation; the ffmpeg CLI
    auto-rotates such frames, PyAV does not. Rotating by ``frame.rotation``
    degrees counter-clockwise reproduces ffmpeg's output exactly (verified
    for 90, -90 and 180).
    """
    image = frame.to_image()
    rotation = int(round(getattr(frame, "rotation", 0) or 0)) % 360
    if rotation:
        image = image.rotate(rotation, expand=True)
    return image


def video_size(path: str | Path) -> tuple[int, int]:
    """Displayed (width, height) of the first video stream (after rotation)."""
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        for frame in container.decode(stream):
            rotation = int(round(getattr(frame, "rotation", 0) or 0)) % 180
            w, h = frame.width, frame.height
            return (h, w) if rotation == 90 else (w, h)
        ctx = stream.codec_context
        return int(ctx.width), int(ctx.height)


def fps(path: str | Path) -> Fraction:
    """Average frame rate of the first video stream (the VFR → CFR rule).

    Falls back to the guessed / base rate when the container reports no
    average (rare; some raw streams), and raises if none is known.
    """
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        for rate in (stream.average_rate, stream.guessed_rate, stream.base_rate):
            if rate:
                return Fraction(rate)
    raise ValueError(f"{path}: video stream has no frame rate")


def grab_frame(path: str | Path, t: float) -> Image.Image:
    """Return the RGB frame on screen at ``t`` seconds (floor semantics).

    That is the last frame whose presentation time is ``<= t``; the first
    frame when ``t`` precedes it and the last frame when ``t`` is past EOF.
    Display-matrix rotation is applied, as the ffmpeg CLI does.
    """
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"  # decoding from the keyframe up to t
        start = (
            float(stream.start_time * stream.time_base)
            if stream.start_time is not None
            else 0.0
        )
        target = max(t, start)
        container.seek(int(target / stream.time_base), stream=stream, backward=True)
        chosen = None
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            if chosen is not None and frame.time > target + 1e-6:
                break
            chosen = frame
            if frame.time > target + 1e-6:
                break  # first decodable frame is already past t
        if chosen is None:
            raise ValueError(f"{path}: no decodable video frame near t={t:.3f}s")
        return _displayed(chosen)
