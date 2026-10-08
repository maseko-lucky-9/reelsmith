"""``app.services.ffmpeg_tools``: bundled-binary runner and PyAV probes."""

from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from app.services import ffmpeg_tools
from tests.fixtures.make_sync_fixture import SyncFixture
from tests.sync_checker import decode_index

_REAL_POPEN = subprocess.Popen

# An ffmpeg that runs until killed: endless synthetic video to the null muxer.
_FOREVER = (
    "ffmpeg", "-hide_banner", "-nostdin", "-re",
    "-f", "lavfi", "-i", "testsrc=size=64x64:rate=25",
    "-f", "null", "-",
)  # fmt: skip


@pytest.fixture
def spawned(monkeypatch) -> list[subprocess.Popen]:
    """Record every Popen ``ffmpeg_tools.run`` creates."""
    ffmpeg_tools.exe()  # imageio-ffmpeg validates the binary with a Popen once
    procs: list[subprocess.Popen] = []
    real = _REAL_POPEN

    def recording_popen(*args, **kwargs):
        proc = real(*args, **kwargs)
        procs.append(proc)
        return proc

    monkeypatch.setattr(ffmpeg_tools.subprocess, "Popen", recording_popen)
    return procs


# ── exe / argv resolution ─────────────────────────────────────────────────────


def test_exe_is_the_imageio_ffmpeg_binary():
    import imageio_ffmpeg

    assert ffmpeg_tools.exe() == imageio_ffmpeg.get_ffmpeg_exe()
    assert Path(ffmpeg_tools.exe()).is_file()


def test_resolve_argv_only_rewrites_bare_ffmpeg():
    assert ffmpeg_tools.resolve_argv(["ffmpeg", "-y"]) == [ffmpeg_tools.exe(), "-y"]
    assert ffmpeg_tools.resolve_argv(["demucs", "-n", "x"]) == ["demucs", "-n", "x"]
    assert ffmpeg_tools.resolve_argv(["/opt/ffmpeg", "-y"]) == ["/opt/ffmpeg", "-y"]


# ── run ───────────────────────────────────────────────────────────────────────


def test_run_success_returns_stderr(tmp_path):
    out = tmp_path / "x.wav"
    ffmpeg_tools.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc",
            "-t",
            "0.1",
            str(out),
        ]
    )
    assert out.stat().st_size > 0


def test_run_failure_raises_with_stderr_tail(tmp_path):
    missing = str(tmp_path / "missing.mp4")
    with pytest.raises(ffmpeg_tools.FfmpegError) as info:
        ffmpeg_tools.run(["ffmpeg", "-hide_banner", "-i", missing, "-f", "null", "-"])
    assert info.value.returncode not in (0, None)
    assert "No such file" in info.value.stderr_tail
    assert "No such file" in str(info.value)


def test_run_kills_on_timeout(spawned):
    with pytest.raises(ffmpeg_tools.FfmpegTimeout):
        ffmpeg_tools.run(_FOREVER, timeout=0.5)
    assert len(spawned) == 1
    assert spawned[0].poll() is not None  # killed and reaped


def test_run_kills_on_cancel_event(spawned):
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    with pytest.raises(ffmpeg_tools.FfmpegCancelled):
        ffmpeg_tools.run(_FOREVER, timeout=30, cancel=cancel)
    assert spawned[0].poll() is not None


def test_run_kills_child_on_base_exception(monkeypatch, spawned):
    """KeyboardInterrupt (or any BaseException) while waiting kills ffmpeg."""
    class InterruptedPopen(_REAL_POPEN):
        calls = 0

        def communicate(self, *args, **kwargs):
            InterruptedPopen.calls += 1
            if InterruptedPopen.calls == 1:
                raise KeyboardInterrupt
            return super().communicate(*args, **kwargs)

    def popen(*args, **kwargs):
        proc = InterruptedPopen(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(ffmpeg_tools.subprocess, "Popen", popen)
    with pytest.raises(KeyboardInterrupt):
        ffmpeg_tools.run(_FOREVER, timeout=30)
    assert spawned[0].poll() is not None


async def test_to_thread_cancellable_kills_ffmpeg_when_task_is_cancelled(spawned):
    t0 = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            ffmpeg_tools.to_thread_cancellable(ffmpeg_tools.run, _FOREVER, timeout=30),
            timeout=0.5,
        )
    # By the time the cancellation has propagated, the child is gone — killed
    # by the cancel signal within a poll interval, not by run()'s own timeout.
    assert time.monotonic() - t0 < 5
    assert len(spawned) == 1
    assert spawned[0].poll() is not None


async def test_to_thread_cancellable_returns_result():
    assert await ffmpeg_tools.to_thread_cancellable(lambda a, b=0: a + b, 2, b=3) == 5


# ── probes ────────────────────────────────────────────────────────────────────


def test_durations(sync_fixture_640: SyncFixture, sync_fixture_720: SyncFixture):
    p640, p720 = sync_fixture_640.path, sync_fixture_720.path
    assert ffmpeg_tools.duration(p640) == pytest.approx(6.006, abs=1e-3)
    assert ffmpeg_tools.duration(p640, "video") == pytest.approx(6.006, abs=1e-3)
    assert ffmpeg_tools.duration(p640, "audio") == pytest.approx(6.006, abs=0.03)
    assert ffmpeg_tools.duration(p720, "video") == pytest.approx(3.003, abs=1e-3)
    assert ffmpeg_tools.duration(p720, "audio") is None
    assert ffmpeg_tools.has_audio(p640) and not ffmpeg_tools.has_audio(p720)
    assert ffmpeg_tools.video_size(p720) == (1280, 720)


def test_fps_cfr(sync_fixture_640: SyncFixture):
    assert ffmpeg_tools.fps(sync_fixture_640.path) == Fraction(24000, 1001)


@pytest.fixture(scope="module")
def vfr_clip(tmp_path_factory) -> Path:
    """60 fps for 1 s, then 20 fps: nominal 60, average ~42.5 fps."""
    out = tmp_path_factory.mktemp("vfr") / "vfr.mp4"
    ffmpeg_tools.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=64x48:rate=60", "-t", "2",
            "-vf", r"setpts='if(lt(N\,60)\,N/60\,1+(N-60)/20)/TB'",
            "-fps_mode", "vfr", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
        ]
    )  # fmt: skip
    return out


def test_fps_vfr_uses_average_rate(vfr_clip: Path):
    with av.open(str(vfr_clip)) as container:
        stream = container.streams.video[0]
        nominal, average = stream.base_rate, stream.average_rate
    assert nominal == 60 and average != nominal
    assert ffmpeg_tools.fps(vfr_clip) == average


def _index(img, fixture: SyncFixture) -> int | None:
    luma = np.asarray(img.convert("L"), dtype=np.float64)
    return decode_index(luma, fixture.geometry)


@pytest.mark.parametrize(
    ("t", "expected"),
    [
        (0.0, 0),
        (37 * 1001 / 24000, 37),  # exactly on frame 37
        (38 * 1001 / 24000 - 0.001, 37),  # just before frame 38 → still 37
        (3.0, 71),  # 3.0 s = frame 71.93 → 71 (floor)
        (99.0, 143),  # past EOF → last frame
    ],
)
def test_grab_frame_floor_semantics(sync_fixture_640: SyncFixture, t, expected):
    img = ffmpeg_tools.grab_frame(sync_fixture_640.path, t)
    assert img.mode == "RGB"
    assert img.size == (640, 360)
    assert _index(img, sync_fixture_640) == expected


# ── display-matrix rotation (phone footage) ───────────────────────────────────


@pytest.fixture(scope="module")
def rotated_640(sync_fixture_640: SyncFixture, tmp_path_factory) -> dict[int, Path]:
    """The 640x360 fixture remuxed with a 90 / -90 / 180 degree display matrix."""
    out = {}
    for rotation in (90, -90, 180):
        path = tmp_path_factory.mktemp("rot") / f"rot{rotation}.mp4"
        ffmpeg_tools.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-display_rotation", str(rotation),
                "-i", str(sync_fixture_640.path), "-c", "copy", str(path),
            ]
        )  # fmt: skip
        out[rotation] = path
    return out


def _ffmpeg_frame(path: Path, t: float, tmp_path: Path) -> np.ndarray:
    """The frame the ffmpeg CLI (which auto-rotates) shows at ``t``."""
    png = tmp_path / f"{path.stem}_{t}.png"
    ffmpeg_tools.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{t:.6f}", "-i", str(path), "-frames:v", "1", "-pix_fmt", "rgb24", str(png),
        ]
    )  # fmt: skip
    from PIL import Image

    return np.asarray(Image.open(png).convert("RGB"))


@pytest.mark.parametrize(
    ("rotation", "size"), [(90, (360, 640)), (-90, (360, 640)), (180, (640, 360))]
)
def test_rotated_source_size_and_frame_match_ffmpeg_autorotate(
    rotated_640, rotation, size, tmp_path
):
    path = rotated_640[rotation]
    assert ffmpeg_tools.video_size(path) == size
    t = 36 * 1001 / 24000  # exactly on frame 36: floor (us) == first pts >= t (-ss)
    got = np.asarray(ffmpeg_tools.grab_frame(path, t))
    want = _ffmpeg_frame(path, t, tmp_path)
    assert got.shape == want.shape == (size[1], size[0], 3)
    # Decoding is bit-identical; only YUV->RGB rounding differs. PyAV >= 18
    # (FFmpeg 8 swscale) is exact BT.601, the bundled ffmpeg 7.1 CLI is off by
    # up to 3 LSB (mean ~1.2). A wrong orientation gives a mean of ~67.
    assert np.abs(got.astype(int) - want.astype(int)).mean() < 2.0


def test_error_message_is_short_but_attribute_keeps_the_tail():
    """JobFailed.error is str(exc): keep it readable; logs keep the full tail."""
    import sys

    noisy = "import sys; sys.stderr.write('x' * 5000 + 'LAST LINE'); sys.exit(3)"
    with pytest.raises(ffmpeg_tools.FfmpegError) as info:
        ffmpeg_tools.run([sys.executable, "-c", noisy])
    assert info.value.returncode == 3
    assert len(info.value.stderr_tail) == 2000
    message = str(info.value)
    assert message.endswith("LAST LINE")
    assert len(message) <= 400


def test_run_with_already_set_cancel_spawns_nothing(spawned):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ffmpeg_tools.FfmpegCancelled):
        ffmpeg_tools.run(_FOREVER, cancel=cancel)
    assert spawned == []


async def test_to_thread_cancellable_drops_a_still_queued_call():
    """With the executor busy, a cancelled call that never started must never
    run, and cancelling it must not wait for the busy worker."""
    from concurrent.futures import ThreadPoolExecutor

    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
    release = threading.Event()
    ran = []
    blocker = asyncio.ensure_future(
        ffmpeg_tools.to_thread_cancellable(release.wait, 10)
    )
    queued = asyncio.ensure_future(
        ffmpeg_tools.to_thread_cancellable(lambda: ran.append("ran"))
    )
    await asyncio.sleep(0.05)
    t0 = time.monotonic()
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert time.monotonic() - t0 < 1.0  # did not wait for the blocker
    release.set()
    await blocker
    await asyncio.sleep(0.05)
    assert ran == []


async def test_cancelled_call_that_raises_logs_no_asyncio_error(caplog):
    """A worker that stops with an exception after the cancel is the expected
    outcome, not an unhandled error: asyncio must not log it (Python 3.14's
    ``asyncio.shield`` logs it as "exception in shielded future")."""
    import logging

    started = threading.Event()

    def stops_on_cancel():
        started.set()
        ffmpeg_tools.current_cancel_event().wait(5)
        raise RuntimeError("stopped by cancel")

    task = asyncio.create_task(ffmpeg_tools.to_thread_cancellable(stops_on_cancel))
    await asyncio.to_thread(started.wait, 5)
    with caplog.at_level(logging.ERROR, logger="asyncio"):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.05)  # let the futures' done-callbacks run

    assert "exception in shielded future" not in caplog.text
