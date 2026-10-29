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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from app.services import ffmpeg_tools, source_check
from app.services.source_check import (
    CheckBusyError,
    CheckUnavailableError,
    FrameVerdict,
    check_clean,
)
from app.settings import settings

VIDEO = "aB8zcAttP1E"
JPEG = b"\xff\xd8fake-jpeg\xff\xd9"


def assert_unavailable(result, cause: str) -> None:
    """``result`` is the answer of a check that could not be made, for ``cause``."""
    assert result.clean is None
    assert result.confidence == 0
    assert result.reason == "check unavailable"
    assert result.frames_checked == 0
    assert result.cause == cause


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

    def __init__(self, script: list, *, delay: float = 0.0, hold: asyncio.Event | None = None) -> None:
        self.script = list(script)
        self.bodies: list[dict] = []
        self.delay = delay
        self.hold = hold  # when set, every call waits for it
        self.running = 0
        self.max_running = 0

    def transport(self) -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.running += 1
            self.max_running = max(self.max_running, self.running)
            try:
                if self.hold is not None:
                    await self.hold.wait()
                if self.delay:
                    await asyncio.sleep(self.delay)
            finally:
                self.running -= 1
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

    assert_unavailable(result, "ollama_unavailable")


async def test_ollama_http_error_is_unavailable(monkeypatch):
    Steps(monkeypatch)
    ollama = FakeOllama([httpx.Response(404, json={"error": "model not found"})] * 4)

    assert_unavailable(await check_clean(VIDEO, transport=ollama.transport()), "ollama_unavailable")


async def test_ollama_timeout_is_unavailable(monkeypatch):
    Steps(monkeypatch)
    monkeypatch.setattr(settings, "source_check_timeout_seconds", 1)
    ollama = FakeOllama([httpx.ReadTimeout("slow")] * 4)

    assert_unavailable(await check_clean(VIDEO, transport=ollama.transport()), "ollama_unavailable")


async def test_malformed_replies_leave_too_few_frames_for_a_verdict(monkeypatch):
    Steps(monkeypatch)
    ollama = FakeOllama(["not json", '{"burned_in_captions": "yes"}', "[]", verdict(False)])

    assert_unavailable(await check_clean(VIDEO, transport=ollama.transport()), "reply_unusable")


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

    assert_unavailable(result, "frames_failed")
    assert ollama.bodies == []


@pytest.mark.parametrize("cause", ["stream_blocked", "stream_failed"])
async def test_no_stream_is_unavailable_with_the_steps_cause(monkeypatch, cause):
    Steps(monkeypatch, stream_error=CheckUnavailableError("yt-dlp rc=1", cause))
    ollama = FakeOllama([])

    assert_unavailable(await check_clean(VIDEO, transport=ollama.transport()), cause)
    assert ollama.bodies == []


async def test_an_unexpected_error_is_unavailable_not_a_500(monkeypatch):
    Steps(monkeypatch, stream_error=RuntimeError("boom"))

    assert_unavailable(await check_clean(VIDEO, transport=FakeOllama([]).transport()), "internal")


async def test_a_check_whose_own_work_outlives_its_budget_is_unavailable_in_about_the_budget(monkeypatch):
    monkeypatch.setattr(settings, "source_check_timeout_seconds", 0.2)
    hang = asyncio.Event()

    async def stuck(video_id: str):
        await hang.wait()

    monkeypatch.setattr(source_check, "resolve_stream", stuck)

    started = time.monotonic()
    result = await asyncio.wait_for(check_clean(VIDEO, transport=FakeOllama([]).transport()), 5)
    elapsed = time.monotonic() - started

    assert_unavailable(result, "timeout")
    assert 0.15 <= elapsed < 1.5, elapsed


async def test_a_model_that_never_answers_is_unavailable_in_about_the_budget(monkeypatch):
    Steps(monkeypatch)
    monkeypatch.setattr(settings, "source_check_timeout_seconds", 0.2)
    ollama = FakeOllama([], hold=asyncio.Event())

    started = time.monotonic()
    result = await asyncio.wait_for(check_clean(VIDEO, transport=ollama.transport()), 5)
    elapsed = time.monotonic() - started

    assert_unavailable(result, "timeout")
    assert 0.15 <= elapsed < 1.5, elapsed


async def test_checks_take_turns_with_the_model_and_queueing_does_not_use_up_the_budget(monkeypatch):
    Steps(monkeypatch)
    monkeypatch.setattr(settings, "source_check_timeout_seconds", 1.0)
    # Each check needs 3 model calls of 0.2 s = 0.6 s with the model, inside the 1 s budget;
    # the second one also waits 0.6 s for the first: 1.2 s in all, past the budget.
    ollama = FakeOllama([verdict(False)] * 6, delay=0.2)

    first, second = await asyncio.gather(
        check_clean(VIDEO, transport=ollama.transport()), check_clean("ZZZZZZZZZZZ", transport=ollama.transport())
    )

    assert first.clean is True
    assert second.clean is True
    assert ollama.max_running == 1


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
    assert (await check_clean(VIDEO, transport=down.transport())).clean is None

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
        source_check._cache_put(f"id{i:09d}", source_check.unavailable("x"))
    source_check._cache_get("id000000000")  # touched: now the newest

    source_check._cache_put("idnew000000", source_check.unavailable("x"))

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
    assert all(r.clean is None for r in results)


# ── limits and pinned values ──────────────────────────────────────────────────


def test_the_limits_are_the_documented_ones():
    # Literals on purpose: each is a promise in the spec.
    assert source_check.MAX_CONCURRENT_CHECKS == 4
    assert source_check.MAX_CONCURRENT_GRABS == 2
    assert source_check.CACHE_SIZE == 256
    assert source_check.CACHE_TTL_SECONDS == 24 * 3600
    assert source_check.FLAGGED_FRAMES_TO_REJECT == 2
    assert source_check.MIN_FRAMES_JUDGED == 2
    assert source_check.FRAME_POSITIONS == (0.15, 0.35, 0.55, 0.75)


def test_every_arm_of_the_stream_selector_is_capped_at_480p():
    arms = source_check.STREAM_SELECTOR.split("/")

    assert len(arms) == 3
    assert all("[height<=480]" in arm for arm in arms[:2])
    # The last resort is the worst quality, never an uncapped best.
    assert arms[2] == "w"


# ── video id ──────────────────────────────────────────────────────────────────

GOOD_IDS = ["aB8zcAttP1E", "-_-_-_-_-_-", "___________", "00000000000", "-moW9jvvMr4"]
# Each is exactly 11 characters: a length check alone does not refuse them.
HOSTILE_IDS = [
    "abcdefghi;k",
    "abcd&t=1s_x",
    "abc/../x/yz",
    "abcdefghij%",
    "abcdefghijé",
    "abcdefghi k",
    "abcdefghi\tk",
    "abcdefghi.k",
    "abcdefghi$k",
    "abcdefghi`k",
    "abcdefghi١k",  # an Arabic-Indic digit: \d and \w match it, [0-9] does not
]


@pytest.mark.parametrize("video_id", GOOD_IDS)
def test_real_ids_match_the_pattern(video_id):
    assert len(video_id) == 11
    assert source_check.VIDEO_ID_RE.fullmatch(video_id)


@pytest.mark.parametrize("video_id", HOSTILE_IDS)
def test_eleven_character_hostile_ids_do_not_match_the_pattern(video_id):
    assert len(video_id) == 11
    assert source_check.VIDEO_ID_RE.fullmatch(video_id) is None


@pytest.mark.parametrize("video_id", HOSTILE_IDS + ["abcdefghijk\n", "short", "twelve_chars"])
async def test_resolve_stream_never_starts_yt_dlp_for_a_hostile_id(monkeypatch, video_id):
    run, calls = _runner(b"100\nhttps://x/y\n")
    monkeypatch.setattr(source_check, "run_bounded", run)

    with pytest.raises(ValueError):
        await source_check.resolve_stream(video_id)

    assert calls == []


# ── stream step ───────────────────────────────────────────────────────────────


def _runner(stdout: bytes, returncode: int = 0, stderr: bytes = b"ERROR: nope"):
    calls: list[tuple[list[str], float]] = []

    async def run(argv, *, timeout):
        calls.append((list(argv), timeout))
        return returncode, stdout, stderr

    return run, calls


async def test_resolve_stream_reads_duration_and_url_from_yt_dlp(monkeypatch):
    run, calls = _runner(b"14184\nhttps://rr1.googlevideo.com/videoplayback?itag=18\n")
    monkeypatch.setattr(source_check, "run_bounded", run)

    url, duration = await source_check.resolve_stream(VIDEO)

    assert (url, duration) == ("https://rr1.googlevideo.com/videoplayback?itag=18", 14184.0)
    ((argv, timeout),) = calls
    # Isolated interpreter, no user config, then the module, our options, and the id after "--".
    assert argv[:5] == [sys.executable, "-I", "-m", "yt_dlp", "--ignore-config"]
    assert argv[-2:] == ["--", f"https://www.youtube.com/watch?v={VIDEO}"]
    assert argv[argv.index("-f") + 1] == source_check.STREAM_SELECTOR
    assert "--no-playlist" in argv
    assert timeout == 30.0


@pytest.mark.parametrize(
    ("stdout", "returncode"),
    [
        (b"", 1),
        (b"NA\nhttps://x/y\n", 0),
        (b"0\nhttps://x/y\n", 0),
        (b"inf\nhttps://x/y\n", 0),
        (b"100\n", 0),
        (b"100\nhttp://insecure/stream\n", 0),
        (b"100\nfile:///etc/passwd\n", 0),
    ],
)
async def test_resolve_stream_refuses_what_it_cannot_use(monkeypatch, stdout, returncode):
    run, _ = _runner(stdout, returncode)
    monkeypatch.setattr(source_check, "run_bounded", run)

    with pytest.raises(CheckUnavailableError) as raised:
        await source_check.resolve_stream(VIDEO)

    assert raised.value.cause == "stream_failed"


@pytest.mark.parametrize(
    "stderr",
    [
        b"ERROR: [youtube] x: Sign in to confirm you\xe2\x80\x99re not a bot. Use --cookies",
        b"WARNING: HTTP Error 429: Too Many Requests\nERROR: unable",
    ],
)
async def test_resolve_stream_says_when_youtube_refused(monkeypatch, stderr):
    run, _ = _runner(b"", 1, stderr)
    monkeypatch.setattr(source_check, "run_bounded", run)

    with pytest.raises(CheckUnavailableError) as raised:
        await source_check.resolve_stream(VIDEO)

    assert raised.value.cause == "stream_blocked"


async def test_resolve_stream_names_its_own_timeout(monkeypatch):
    async def slow(argv, *, timeout):
        raise TimeoutError

    monkeypatch.setattr(source_check, "run_bounded", slow)

    with pytest.raises(CheckUnavailableError) as raised:
        await source_check.resolve_stream(VIDEO)

    assert raised.value.cause == "timeout"
    assert "yt-dlp" in str(raised.value)


# ── frame step ────────────────────────────────────────────────────────────────


def test_frames_come_from_15_35_55_and_75_percent_of_the_duration():
    assert source_check.frame_times(200.0) == [30.0, 70.0, 110.0, 150.0]


class FfmpegStub:
    """Replaces ``run_bounded`` for the ffmpeg grabs: writes the output file and tracks overlap."""

    def __init__(self, *, fail_every_other: bool = False, delay: float = 0.0) -> None:
        self.calls: list[tuple[list[str], float]] = []
        self.fail_every_other = fail_every_other
        self.delay = delay
        self.running = 0
        self.max_running = 0

    async def __call__(self, argv, *, timeout):
        self.calls.append((list(argv), timeout))
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        finally:
            self.running -= 1
        if self.fail_every_other and len(self.calls) % 2:
            return 1, b"", b"boom"
        Path(argv[-1]).write_bytes(JPEG)
        return 0, b"", b""


async def test_remote_frames_seek_before_the_input_through_the_bundled_ffmpeg(monkeypatch):
    stub = FfmpegStub()
    monkeypatch.setattr(source_check, "run_bounded", stub)

    frames = await source_check.extract_frames("https://rr1.googlevideo.com/videoplayback?x=1", 100.0)

    assert frames == [JPEG] * 4
    assert len(stub.calls) == 4
    ordered = sorted(stub.calls, key=lambda c: float(c[0][c[0].index("-ss") + 1]))
    for (argv, timeout), t in zip(ordered, (15.0, 35.0, 55.0, 75.0), strict=True):
        assert argv[0] == ffmpeg_tools.exe()  # the bundled binary, never a system ffmpeg
        assert float(argv[argv.index("-ss") + 1]) == t
        assert argv.index("-ss") < argv.index("-i")
        assert argv[argv.index("-protocol_whitelist") + 1] == "https,tls,tcp,crypto"
        assert argv[argv.index("-frames:v") + 1] == "1"
        assert timeout == 30.0


async def test_at_most_two_ffmpeg_children_run_at_once_across_checks(monkeypatch):
    stub = FfmpegStub(delay=0.05)
    monkeypatch.setattr(source_check, "run_bounded", stub)

    await asyncio.gather(*(source_check.extract_frames("https://x/y", 100.0) for _ in range(3)))

    assert len(stub.calls) == 12
    assert stub.max_running == source_check.MAX_CONCURRENT_GRABS == 2


def _make_video(path: Path) -> None:
    ffmpeg_tools.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", "10", str(path)], timeout=60
    )


async def test_extract_frames_decodes_a_real_video(tmp_path):
    video = tmp_path / "t.mp4"
    _make_video(video)

    frames = await source_check.extract_frames(str(video), 10.0)

    assert len(frames) == 4
    assert all(f.startswith(b"\xff\xd8") for f in frames)


async def test_frame_grabs_do_not_use_the_default_thread_pool_the_pipeline_shares(tmp_path):
    video = tmp_path / "t.mp4"
    _make_video(video)
    loop = asyncio.get_running_loop()
    pool = ThreadPoolExecutor(max_workers=1)  # stands in for the pipeline's pool, here fully busy
    loop.set_default_executor(pool)
    release = threading.Event()
    busy = loop.run_in_executor(None, release.wait)
    try:
        frames = await asyncio.wait_for(source_check.extract_frames(str(video), 10.0), timeout=30)
    finally:
        release.set()
        await busy
        pool.shutdown()

    assert len(frames) == 4


async def test_extract_frames_keeps_the_frames_that_worked(monkeypatch):
    monkeypatch.setattr(source_check, "run_bounded", FfmpegStub(fail_every_other=True))

    frames = await source_check.extract_frames("https://x/y", 100.0)

    assert len(frames) == 2


async def test_extract_frames_of_a_missing_file_is_empty(tmp_path):
    assert await source_check.extract_frames(str(tmp_path / "missing.mp4"), 10.0) == []


# ── reply parsing ─────────────────────────────────────────────────────────────


def test_a_reason_is_flattened_and_capped_at_200_characters():
    raw = json.dumps({"burned_in_captions": True, "confidence": 0.5, "reason": "a\x00b\nc\td\x1b[31m " + "x" * 500})

    parsed = source_check.parse_verdict(raw)

    assert parsed is not None
    assert len(parsed.reason) == 200
    assert parsed.reason.startswith("a b c d [31m xxx")
    assert all(ch.isprintable() for ch in parsed.reason)


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [(7, 1.0), (-3, 0.0), (0.25, 0.25), ("high", 0.5), (True, 0.5), (None, 0.5), ("NaN", 0.5)],
)
def test_a_confidence_is_clamped_to_0_1_and_a_bad_one_is_half(confidence, expected):
    raw = json.dumps({"burned_in_captions": False, "confidence": confidence, "reason": ""})

    assert source_check.parse_verdict(raw).confidence == expected


def test_a_non_finite_json_confidence_is_half():
    raw = '{"burned_in_captions": false, "confidence": NaN, "reason": ""}'

    assert source_check.parse_verdict(raw).confidence == 0.5


# ── cancelling and the cache clock ────────────────────────────────────────────


async def test_a_client_that_hangs_up_does_not_kill_the_shared_check(monkeypatch):
    Steps(monkeypatch)
    hold = asyncio.Event()
    ollama = FakeOllama([verdict(False)] * 4, hold=hold)
    waiting = asyncio.create_task(check_clean(VIDEO, transport=ollama.transport()))
    while ollama.running == 0:  # the check has reached the model
        await asyncio.sleep(0.01)

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    hold.set()
    await asyncio.wait_for(source_check._inflight[VIDEO], timeout=5)
    await asyncio.sleep(0)

    cached = source_check._cache_get(VIDEO)
    assert cached is not None and cached.clean is True


async def test_a_cached_answer_expires_after_24_hours(monkeypatch):
    steps = Steps(monkeypatch)
    clock = {"now": 1000.0}
    monkeypatch.setattr(source_check, "_now", lambda: clock["now"])
    ollama = FakeOllama([verdict(False)] * 12)

    await check_clean(VIDEO, transport=ollama.transport())
    clock["now"] += 24 * 3600 - 1
    await check_clean(VIDEO, transport=ollama.transport())
    assert steps.resolve_calls == [VIDEO]  # still fresh a second before the day is out

    clock["now"] += 2
    await check_clean(VIDEO, transport=ollama.transport())

    assert steps.resolve_calls == [VIDEO, VIDEO]


# ── vote ──────────────────────────────────────────────────────────────────────


def test_fewer_than_two_judged_frames_decide_nothing():
    assert_unavailable(source_check.decide([]), "reply_unusable")
    assert_unavailable(source_check.decide([FrameVerdict(False, 0.9, "")]), "reply_unusable")


def test_two_judged_frames_that_disagree_are_no_majority():
    assert_unavailable(
        source_check.decide([FrameVerdict(True, 0.9, "a"), FrameVerdict(False, 0.9, "b")]), "inconclusive"
    )


def test_two_judged_frames_without_a_flag_are_clean():
    result = source_check.decide([FrameVerdict(False, 0.8, ""), FrameVerdict(False, 0.6, "")])

    assert (result.clean, result.frames_checked, result.confidence) == (True, 2, 0.7)


def test_two_judged_frames_both_flagged_reject():
    result = source_check.decide([FrameVerdict(True, 0.9, "a"), FrameVerdict(True, 0.7, "b")])

    assert result.clean is False
    assert result.frames_checked == 2
