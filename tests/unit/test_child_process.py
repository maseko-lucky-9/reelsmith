"""Killable child processes (spec 014): stream lines; on cancel SIGTERM, then SIGKILL the group."""

from __future__ import annotations

import asyncio
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

from app.services import child_process


def _script(tmp_path: Path, body: str) -> list[str]:
    path = tmp_path / "child.py"
    path.write_text(textwrap.dedent(body))
    return [sys.executable, str(path), str(tmp_path)]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _wait_for(path: Path) -> None:
    for _ in range(500):
        if path.exists() and path.read_text():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{path} never appeared")


async def test_lines_are_streamed_and_the_exit_status_and_stderr_returned(tmp_path):
    argv = _script(
        tmp_path,
        """
        import sys
        print("one", flush=True)
        print("", flush=True)
        print("two", flush=True)
        print("oops", file=sys.stderr)
        sys.exit(3)
        """,
    )
    lines: list[str] = []
    code, err = await child_process.run_streaming(argv, timeout=30, on_line=lines.append)
    assert (code, err, lines) == (3, "oops", ["one", "two"])


async def test_cancel_sends_sigterm_first_so_the_child_can_clean_up(tmp_path):
    argv = _script(
        tmp_path,
        """
        import os, signal, sys, time
        from pathlib import Path
        out = Path(sys.argv[1])
        def term(signum, frame):
            (out / "cleaned").write_text("yes")
            sys.exit(143)
        signal.signal(signal.SIGTERM, term)
        (out / "pid").write_text(str(os.getpid()))
        print("started", flush=True)
        time.sleep(60)
        """,
    )
    task = asyncio.create_task(child_process.run_streaming(argv, timeout=60, on_line=lambda line: None))
    await _wait_for(tmp_path / "pid")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (tmp_path / "cleaned").read_text() == "yes"
    assert not _alive(int((tmp_path / "pid").read_text()))


async def test_a_child_ignoring_sigterm_and_its_grandchild_are_killed_after_the_grace(tmp_path):
    argv = _script(
        tmp_path,
        """
        import os, signal, subprocess, sys, time
        from pathlib import Path
        out = Path(sys.argv[1])
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        grand = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        (out / "pids").write_text(f"{os.getpid()} {grand.pid}")
        time.sleep(60)
        """,
    )
    task = asyncio.create_task(
        child_process.run_streaming(argv, timeout=60, on_line=lambda line: None, terminate_grace=0.3)
    )
    await _wait_for(tmp_path / "pids")
    child, grandchild = (int(p) for p in (tmp_path / "pids").read_text().split())
    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # A grandchild that kept the pipes open would hold the cancel until it ended.
    assert time.monotonic() - started < 10
    assert not _alive(child)
    for _ in range(100):  # the grandchild is reaped by init, not by us
        if not _alive(grandchild):
            break
        await asyncio.sleep(0.02)
    assert not _alive(grandchild)


async def test_a_long_line_without_newline_is_no_error(tmp_path):
    """Review finding: a 200 KB carriage-return progress bar on stderr (a model
    download) must not raise LimitOverrunError and fail the caller."""
    argv = _script(
        tmp_path,
        """
        import sys
        sys.stderr.write("x" * 200_000)
        sys.stderr.write("\\rdownloading 50%\\rdone\\n")
        print("y" * 100_000, flush=True)
        print("ok", flush=True)
        """,
    )
    lines: list[str] = []
    code, err = await child_process.run_streaming(argv, timeout=30, on_line=lines.append)
    assert code == 0 and lines[-1] == "ok" and len(lines[0]) == 100_000
    assert err.splitlines()[-2:] == ["downloading 50%", "done"]


async def test_the_group_dies_even_when_the_child_obeys_sigterm(tmp_path):
    """Review finding: a child that exits on SIGTERM must not leave its grandchild."""
    argv = _script(
        tmp_path,
        """
        import os, subprocess, sys, time
        from pathlib import Path
        grand = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (Path(sys.argv[1]) / "pids").write_text(f"{os.getpid()} {grand.pid}")
        time.sleep(30)
        """,
    )
    task = asyncio.create_task(child_process.run_streaming(argv, timeout=60, on_line=lambda line: None))
    await _wait_for(tmp_path / "pids")
    _, grandchild = (int(p) for p in (tmp_path / "pids").read_text().split())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(100):
        if not _alive(grandchild):
            break
        await asyncio.sleep(0.02)
    assert not _alive(grandchild)


async def test_a_timeout_stops_the_child_and_raises(tmp_path):
    argv = _script(
        tmp_path,
        """
        import os, sys, time
        from pathlib import Path
        (Path(sys.argv[1]) / "pid").write_text(str(os.getpid()))
        time.sleep(60)
        """,
    )
    with pytest.raises(TimeoutError):
        await child_process.run_streaming(argv, timeout=3, on_line=lambda line: None, terminate_grace=0.2)
    assert not _alive(int((tmp_path / "pid").read_text()))


async def test_a_multibyte_character_split_across_reads_stays_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(child_process, "READ_CHUNK_BYTES", 1)  # every byte its own read
    argv = _script(tmp_path, """
        import sys
        sys.stdout.buffer.write("café ✓ naïve\\n".encode("utf-8"))
        sys.stderr.buffer.write("erreur é".encode("utf-8"))
        """)  # fmt: skip
    lines: list[str] = []
    code, err = await child_process.run_streaming(argv, timeout=30, on_line=lines.append)
    assert (code, lines, err) == (0, ["café ✓ naïve"], "erreur é")


_SLOW_CLEANUP = """
    import os, signal, sys, time
    from pathlib import Path
    out = Path(sys.argv[1])
    def term(signum, frame):
        time.sleep(0.5)  # cleaning up takes a while
        (out / "cleaned").write_text("yes")
        sys.exit(143)
    signal.signal(signal.SIGTERM, term)
    (out / "pid").write_text(str(os.getpid()))
    time.sleep(60)
    """


async def test_a_child_gets_its_grace_to_clean_up_before_sigkill(tmp_path):
    argv = _script(tmp_path, _SLOW_CLEANUP)
    task = asyncio.create_task(
        child_process.run_streaming(argv, timeout=60, on_line=lambda line: None, terminate_grace=3.0)
    )
    await _wait_for(tmp_path / "pid")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (tmp_path / "cleaned").read_text() == "yes"  # not SIGKILLed during its 0.5 s cleanup
    assert not _alive(int((tmp_path / "pid").read_text()))


async def test_a_second_cancel_does_not_cut_the_stop_short(tmp_path):
    argv = _script(tmp_path, _SLOW_CLEANUP)
    task = asyncio.create_task(
        child_process.run_streaming(argv, timeout=60, on_line=lambda line: None, terminate_grace=3.0)
    )
    await _wait_for(tmp_path / "pid")
    task.cancel()
    await asyncio.sleep(0.1)  # inside the grace: SIGTERM sent, the child is cleaning up
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The stop ran to the end: the child finished its cleanup and was reaped.
    assert (tmp_path / "cleaned").read_text() == "yes"
    assert not _alive(int((tmp_path / "pid").read_text()))
