"""The clean-footage check (spec 015): stream, frames, vision vote, cache.

Nothing here touches YouTube or a real Ollama: the stream step and the frame
step are replaced, and Ollama is an ``httpx.MockTransport``. ``extract_frames``
also runs once against a generated video with the bundled ffmpeg.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import httpx
import pytest

from app.services import ffmpeg_tools, source_check
from app.services.source_check import (
    UNAVAILABLE,
    CheckBusyError,
    CheckUnavailableError,
    FrameVerdict,
    check_clean,
)
from app.settings import settings

VIDEO = "aB8zcAttP1E"
JPEG = b"\xff\xd8fake-jpeg\xff\xd9"


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    source_check.clear_cache()
    source_check._inflight.clear()
    monkeypatch.setattr(settings, "source_check_model", "vision-test:1b")
    monkeypatch.setattr(settings, "source_check_timeout_seconds", 5)
    yield
    source_check.clear_cache()


class Steps:
    """Replaces the stream and frame steps; counts calls."""

    def __init__(self, monkeypatch, frames: int = 4, stream_error: Exception | None = None) -> None:
        self.resolve_calls: list[str] = []
        self.frames = frames
        self.stream_error = stream_error

        async def resolve(video_id: str):
            self.resolve_calls.append(video_id)
            if self.stream_error is not None:
                raise self.stream_error
            return "https://rr1.googlevideo.com/videoplayback?x=1", 100.0

        async def extract(source: str, duration: float):
            return [JPEG + bytes([i]) for i in range(self.frames)]

        monkeypatch.setattr(source_check, "resolve_stream", resolve)
        monkeypatch.setattr(source_check, "extract_frames", extract)


class FakeOllama:
    """An Ollama that answers each call with the next verdict in ``script``."""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.bodies: list[dict] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.bodies.append(json.loads(request.content))
            item = self.script.pop(0) if self.script else self.script_default
            if isinstance(item, Exception):
                raise item
            if isinstance(item, httpx.Response):
                return item
            return httpx.Response(200, json={"response": item if isinstance(item, str) else json.dumps(item)})

        return httpx.MockTransport(handler)

    script_default = {"burned_in_captions": False, "confidence": 0.9, "reason": "plain picture"}


def verdict(captions: bool, confidence: float = 0.9, reason: str = "r") -> dict:
    return {"burned_in_captions": captions, "confidence": confidence, "reason": reason}


async def test_a_video_without_captions_is_clean(monkeypatch):
    Steps(monkeypatch)
    ollama = FakeOllama([verdict(False)] * 4)

    result = await check_clean(VIDEO, transport=ollama.transport())

    # Three clean frames settle it: one more could add at most one flag, short of the two needed.
    assert result.clean is True
    assert result.frames_checked == 3
    assert result.confidence == 0.9
    assert "no burned-in captions in 3 of 3" in result.reason
    assert len(ollama.bodies) == 3


async def test_a_video_with_captions_in_every_frame_is_not_clean(monkeypatch):
    Steps(monkeypatch)
    ollama = FakeOllama([verdict(True, 0.95, "white words at the bottom")] * 4)

    result = await check_clean(VIDEO, transport=ollama.transport())

    # Two flagged frames settle it: the other two are never sent to the model.
    assert result.clean is False
    assert result.frames_checked == 2
    assert len(ollama.bodies) == 2
    assert result.confidence == 0.95
    assert "white words at the bottom" in result.reason


@pytest.mark.parametrize(
    ("flags", "clean"),
    [
        ((False, False, False, False), True),
        ((True, False, False, False), True),  # one flagged frame is a logo or a lucky frame
        ((True, True, False, False), False),  # two of four reject
        ((False, True, False, True), False),
        ((True, True, True, False), False),
        ((True, True, True, True), False),
    ],
)
async def test_two_flagged_frames_of_four_make_a_video_not_clean(monkeypatch, flags, clean):
    Steps(monkeypatch)
    ollama = FakeOllama([verdict(f) for f in flags])

    result = await check_clean(VIDEO, transport=ollama.transport())

    assert result.clean is clean


async def test_a_mixed_vote_reports_the_agreeing_share_as_confidence(monkeypatch):
    Steps(monkeypatch)
    # 3 clean frames at 0.8 of 4: 0.75 * 0.8 = 0.6
    ollama = FakeOllama([verdict(True, 0.5), verdict(False, 0.8), verdict(False, 0.8), verdict(False, 0.8)])

    result = await check_clean(VIDEO, transport=ollama.transport())

    assert result.clean is True
    assert result.confidence == 0.6


async def test_each_call_sends_the_frame_with_the_schema_and_no_thinking(monkeypatch):
    Steps(monkeypatch)
    # One flagged frame keeps the vote open until the fourth frame.
    ollama = FakeOllama([verdict(True)] + [verdict(False)] * 3)

    await check_clean(VIDEO, transport=ollama.transport())

    first = ollama.bodies[0]
    assert first["model"] == "vision-test:1b"
    assert first["images"] == [base64.b64encode(JPEG + b"\x00").decode()]
    assert first["stream"] is False
    assert first["think"] is False
    assert first["keep_alive"] == "60s"
    assert first["options"] == {"temperature": 0}
    assert first["format"]["required"] == ["reason", "burned_in_captions", "confidence"]
    assert first["format"]["properties"]["burned_in_captions"] == {"type": "boolean"}
    # The model writes what it sees (reason) before it decides.
    assert list(first["format"]["properties"])[0] == "reason"
    # Logos, watermarks, title cards and lower thirds must be named as "not captions".
    for word in ("logos", "watermarks", "title cards", "lower-third"):
        assert word in first["prompt"]
    # The four calls carry four different frames.
    assert len({tuple(b["images"]) for b in ollama.bodies}) == 4


async def test_ollama_down_is_unavailable_not_an_error(monkeypatch):
    Steps(monkeypatch)
    ollama = FakeOllama([httpx.ConnectError("refused")] * 4)

    result = await check_clean(VIDEO, transport=ollama.transport())

    assert result == UNAVAILABLE
    assert result.clean is None
    assert result.confidence == 0
    assert result.reason == "check unavailable"


async def test_ollama_http_error_is_unavailable(monkeypatch):
    Steps(monkeypatch)
    ollama = FakeOllama([httpx.Response(404, json={"error": "model not found"})] * 4)

    assert await check_clean(VIDEO, transport=ollama.transport()) == UNAVAILABLE


async def test_ollama_timeout_is_unavailable(monkeypatch):
    Steps(monkeypatch)
    monkeypatch.setattr(settings, "source_check_timeout_seconds", 1)
    ollama = FakeOllama([httpx.ReadTimeout("slow")] * 4)

    assert await check_clean(VIDEO, transport=ollama.transport()) == UNAVAILABLE


async def test_malformed_replies_leave_too_few_frames_for_a_verdict(monkeypatch):
    Steps(monkeypatch)
    ollama = FakeOllama(["not json", '{"burned_in_captions": "yes"}', "[]", verdict(False)])

    assert await check_clean(VIDEO, transport=ollama.transport()) == UNAVAILABLE


async def test_one_malformed_reply_is_skipped_and_the_rest_decide(monkeypatch):
    Steps(monkeypatch)
    ollama = FakeOllama(["{broken", verdict(True), verdict(True), verdict(False)])

    result = await check_clean(VIDEO, transport=ollama.transport())

    assert result.clean is False
    assert result.frames_checked == 2


async def test_a_missing_confidence_does_not_discard_the_verdict(monkeypatch):
    Steps(monkeypatch)
    ollama = FakeOllama([{"burned_in_captions": False, "reason": "x"}] * 4)

    result = await check_clean(VIDEO, transport=ollama.transport())

    assert result.clean is True


async def test_frame_extraction_failure_is_unavailable_and_asks_ollama_nothing(monkeypatch):
    Steps(monkeypatch, frames=1)
    ollama = FakeOllama([verdict(False)] * 4)

    result = await check_clean(VIDEO, transport=ollama.transport())

    assert result == UNAVAILABLE
    assert ollama.bodies == []


async def test_no_stream_is_unavailable(monkeypatch):
    Steps(monkeypatch, stream_error=CheckUnavailableError("yt-dlp rc=1"))
    ollama = FakeOllama([])

    assert await check_clean(VIDEO, transport=ollama.transport()) == UNAVAILABLE
    assert ollama.bodies == []


async def test_a_check_that_outlives_its_budget_is_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "source_check_timeout_seconds", 0.05)

    async def hang(video_id: str):
        await asyncio.sleep(30)

    monkeypatch.setattr(source_check, "resolve_stream", hang)

    assert await check_clean(VIDEO, transport=FakeOllama([]).transport()) == UNAVAILABLE


async def test_a_second_request_for_the_same_id_does_not_ask_ollama_again(monkeypatch):
    steps = Steps(monkeypatch)
    ollama = FakeOllama([verdict(False)] * 8)

    first = await check_clean(VIDEO, transport=ollama.transport())
    second = await check_clean(VIDEO, transport=ollama.transport())

    assert second == first
    assert len(ollama.bodies) == 3
    assert steps.resolve_calls == [VIDEO]


async def test_an_unavailable_answer_is_not_cached(monkeypatch):
    steps = Steps(monkeypatch)
    down = FakeOllama([httpx.ConnectError("refused")] * 4)
    assert await check_clean(VIDEO, transport=down.transport()) == UNAVAILABLE

    up = FakeOllama([verdict(False)] * 4)
    result = await check_clean(VIDEO, transport=up.transport())

    assert result.clean is True
    assert steps.resolve_calls == [VIDEO, VIDEO]


async def test_two_concurrent_requests_for_one_id_share_one_check(monkeypatch):
    steps = Steps(monkeypatch)
    ollama = FakeOllama([verdict(False)] * 8)

    a, b = await asyncio.gather(
        check_clean(VIDEO, transport=ollama.transport()), check_clean(VIDEO, transport=ollama.transport())
    )

    assert a == b
    assert steps.resolve_calls == [VIDEO]
    assert len(ollama.bodies) == 3


async def test_the_cache_keeps_at_most_256_videos_and_drops_the_least_recently_used(monkeypatch):
    for i in range(source_check.CACHE_SIZE):
        source_check._cache_put(f"id{i:09d}", UNAVAILABLE)
    source_check._cache_get("id000000000")  # touched: now the newest

    source_check._cache_put("idnew000000", UNAVAILABLE)

    assert len(source_check._cache) == source_check.CACHE_SIZE
    assert "id000000000" in source_check._cache
    assert "id000000001" not in source_check._cache


async def test_a_fifth_distinct_video_is_refused_while_four_checks_run(monkeypatch):
    release = asyncio.Event()

    async def slow(video_id: str):
        await release.wait()
        raise CheckUnavailableError("done")

    monkeypatch.setattr(source_check, "resolve_stream", slow)
    running = [asyncio.create_task(check_clean(f"vid{i:08d}")) for i in range(source_check.MAX_CONCURRENT_CHECKS)]
    await asyncio.sleep(0.05)

    with pytest.raises(CheckBusyError):
        await check_clean("vidOther0000")
    # The same id as a running check joins it instead of being refused.
    joined = asyncio.create_task(check_clean("vid00000000"))

    release.set()
    results = await asyncio.gather(*running, joined)
    assert all(r == UNAVAILABLE for r in results)


# ── stream step ───────────────────────────────────────────────────────────────


def _runner(stdout: bytes, returncode: int = 0):
    calls: list[tuple[list[str], float]] = []

    async def run(argv, *, timeout):
        calls.append((list(argv), timeout))
        return returncode, stdout, b"ERROR: nope"

    return run, calls


async def test_resolve_stream_reads_duration_and_url_from_yt_dlp(monkeypatch):
    run, calls = _runner(b"14184\nhttps://rr1.googlevideo.com/videoplayback?itag=18\n")
    monkeypatch.setattr(source_check, "run_bounded", run)

    url, duration = await source_check.resolve_stream(VIDEO)

    assert (url, duration) == ("https://rr1.googlevideo.com/videoplayback?itag=18", 14184.0)
    argv, _ = calls[0]
    assert argv[:3] == [sys.executable, "-m", "yt_dlp"]
    assert argv[-2:] == ["--", f"https://www.youtube.com/watch?v={VIDEO}"]
    assert argv[argv.index("-f") + 1] == source_check.STREAM_SELECTOR
    assert "height<=480" in source_check.STREAM_SELECTOR
    assert "--no-playlist" in argv


@pytest.mark.parametrize(
    ("stdout", "returncode"),
    [
        (b"", 1),
        (b"NA\nhttps://x/y\n", 0),
        (b"0\nhttps://x/y\n", 0),
        (b"100\n", 0),
        (b"100\nhttp://insecure/stream\n", 0),
        (b"100\nfile:///etc/passwd\n", 0),
    ],
)
async def test_resolve_stream_refuses_what_it_cannot_use(monkeypatch, stdout, returncode):
    run, _ = _runner(stdout, returncode)
    monkeypatch.setattr(source_check, "run_bounded", run)

    with pytest.raises(CheckUnavailableError):
        await source_check.resolve_stream(VIDEO)


# ── frame step ────────────────────────────────────────────────────────────────


def test_frames_come_from_15_35_55_and_75_percent_of_the_duration():
    assert source_check.frame_times(200.0) == [30.0, 70.0, 110.0, 150.0]


async def test_remote_frames_seek_before_the_input_through_the_bundled_ffmpeg(monkeypatch):
    argvs: list[list[str]] = []

    def fake_run(argv, *, timeout=None, cancel=None, cwd=None):
        argvs.append(list(argv))
        Path(argv[-1]).write_bytes(JPEG)
        return ""

    monkeypatch.setattr(ffmpeg_tools, "run", fake_run)

    frames = await source_check.extract_frames("https://rr1.googlevideo.com/videoplayback?x=1", 100.0)

    assert frames == [JPEG] * 4
    assert len(argvs) == 4
    for argv, t in zip(sorted(argvs, key=lambda a: float(a[a.index("-ss") + 1])), (15.0, 35.0, 55.0, 75.0)):
        assert argv[0] == "ffmpeg"  # resolved to the bundled binary by ffmpeg_tools.run
        assert float(argv[argv.index("-ss") + 1]) == t
        assert argv.index("-ss") < argv.index("-i")
        assert argv[argv.index("-protocol_whitelist") + 1] == "https,tls,tcp,crypto"
        assert argv[argv.index("-frames:v") + 1] == "1"


async def test_extract_frames_decodes_a_real_video(tmp_path):
    video = tmp_path / "t.mp4"
    ffmpeg_tools.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", "10", str(video)], timeout=60
    )

    frames = await source_check.extract_frames(str(video), 10.0)

    assert len(frames) == 4
    assert all(f.startswith(b"\xff\xd8") for f in frames)


async def test_extract_frames_keeps_the_frames_that_worked(monkeypatch):
    seen = {"n": 0}

    def flaky(argv, *, timeout=None, cancel=None, cwd=None):
        seen["n"] += 1
        if seen["n"] % 2:
            raise ffmpeg_tools.FfmpegError("boom", returncode=1, stderr_tail="")
        Path(argv[-1]).write_bytes(JPEG)
        return ""

    monkeypatch.setattr(ffmpeg_tools, "run", flaky)

    frames = await source_check.extract_frames("https://x/y", 100.0)

    assert len(frames) == 2


async def test_extract_frames_of_a_missing_file_is_empty(tmp_path):
    assert await source_check.extract_frames(str(tmp_path / "missing.mp4"), 10.0) == []


# ── vote ──────────────────────────────────────────────────────────────────────


def test_fewer_than_two_judged_frames_decide_nothing():
    assert source_check.decide([]) == UNAVAILABLE
    assert source_check.decide([FrameVerdict(False, 0.9, "")]) == UNAVAILABLE


def test_two_judged_frames_both_flagged_reject():
    result = source_check.decide([FrameVerdict(True, 0.9, "a"), FrameVerdict(True, 0.7, "b")])

    assert result.clean is False
    assert result.frames_checked == 2
