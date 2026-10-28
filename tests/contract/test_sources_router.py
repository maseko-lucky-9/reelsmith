"""Contract tests for ``GET /sources/youtube/search`` and ``.../{id}/clean`` (spec 015).

yt-dlp is never started: ``run_bounded`` is replaced by a recorder. The point
of the bad-input cases is that nothing hostile reaches a subprocess argv.
"""

from __future__ import annotations

import json
import sys

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.routers import sources
from app.services import source_check
from app.settings import settings

ID_A = "aB8zcAttP1E"
ID_B = "NMHiLvirCb0"


def _flat_search(*entries: dict) -> bytes:
    return json.dumps({"_type": "playlist", "entries": list(entries)}).encode()


class Recorder:
    """Stands in for ``run_bounded``; remembers every call."""

    def __init__(self, stdout: bytes = b"", returncode: int = 0, error: Exception | None = None) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.error = error
        self.calls: list[tuple[list[str], float]] = []

    async def __call__(self, argv, *, timeout):
        self.calls.append((list(argv), timeout))
        if self.error is not None:
            raise self.error
        return self.returncode, self.stdout, b"boom"


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def _install(monkeypatch, recorder: Recorder) -> Recorder:
    monkeypatch.setattr(sources, "run_bounded", recorder)
    return recorder


def test_search_maps_yt_dlp_entries_to_items(client, monkeypatch):
    recorder = _install(
        monkeypatch,
        Recorder(
            _flat_search(
                {"id": ID_A, "title": "Talk A", "duration": 12867, "channel": "Lex Fridman", "url": "x"},
                {"id": ID_B, "title": "Talk B", "duration": None, "uploader": "Someone"},
                {"id": "UC-channelid-too-long", "title": "A channel, not a video"},
                {"id": "short", "title": "bad id"},
                "not a dict",
            )
        ),
    )

    response = client.get("/sources/youtube/search", params={"q": "lex fridman", "limit": 5})

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": ID_A,
                "title": "Talk A",
                "channel": "Lex Fridman",
                "duration_seconds": 12867,
                "thumbnail_url": f"https://i.ytimg.com/vi/{ID_A}/mqdefault.jpg",
                "url": f"https://www.youtube.com/watch?v={ID_A}",
            },
            {
                "id": ID_B,
                "title": "Talk B",
                "channel": "Someone",
                "duration_seconds": None,
                "thumbnail_url": f"https://i.ytimg.com/vi/{ID_B}/mqdefault.jpg",
                "url": f"https://www.youtube.com/watch?v={ID_B}",
            },
        ]
    }
    ((argv, timeout),) = recorder.calls
    assert argv[:3] == [sys.executable, "-m", "yt_dlp"]
    # The term is the last argument, behind "--", prefixed so it is never a URL or an option.
    assert argv[-2:] == ["--", "ytsearch5:lex fridman"]
    assert "--flat-playlist" in argv and "-J" in argv
    assert timeout == settings.youtube_search_timeout_seconds


def test_search_limit_defaults_to_twelve_and_the_api_prefix_works(client, monkeypatch):
    recorder = _install(monkeypatch, Recorder(_flat_search()))

    response = client.get("/api/sources/youtube/search", params={"q": "cats"})

    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert recorder.calls[0][0][-1] == "ytsearch12:cats"


def test_search_passes_shell_metacharacters_as_one_inert_argument(client, monkeypatch):
    recorder = _install(monkeypatch, Recorder(_flat_search()))

    client.get("/sources/youtube/search", params={"q": "cats; rm -rf / && $(id) `id` | tee x"})

    argv = recorder.calls[0][0]
    assert argv[-1] == "ytsearch12:cats; rm -rf / && $(id) `id` | tee x"
    assert len(argv) == 8


def test_search_timeout_is_504_and_failure_is_502(client, monkeypatch):
    _install(monkeypatch, Recorder(error=TimeoutError()))
    assert client.get("/sources/youtube/search", params={"q": "x"}).status_code == 504

    _install(monkeypatch, Recorder(returncode=1))
    assert client.get("/sources/youtube/search", params={"q": "x"}).status_code == 502

    _install(monkeypatch, Recorder(b"<html>not json"))
    assert client.get("/sources/youtube/search", params={"q": "x"}).status_code == 502


@pytest.mark.parametrize(
    "q",
    [
        "--exec",
        "-x",
        "  --exec touch /tmp/pwned",
        "line one\nline two",
        "tab\there",
        "nul\x00byte",
        "bell\x07",
        "   ",
        "",
        "x" * 201,
    ],
)
def test_search_refuses_a_query_that_could_be_an_option_or_a_second_argument(client, monkeypatch, q):
    recorder = _install(monkeypatch, Recorder(_flat_search()))

    response = client.get("/sources/youtube/search", params={"q": q})

    assert response.status_code in (400, 422)
    assert recorder.calls == []


def test_search_accepts_a_query_of_exactly_200_characters(client, monkeypatch):
    recorder = _install(monkeypatch, Recorder(_flat_search()))

    assert client.get("/sources/youtube/search", params={"q": "x" * 200}).status_code == 200
    assert len(recorder.calls) == 1


@pytest.mark.parametrize("limit", [0, 51, -1, "abc"])
def test_search_refuses_a_limit_outside_1_to_50(client, monkeypatch, limit):
    recorder = _install(monkeypatch, Recorder(_flat_search()))

    assert client.get("/sources/youtube/search", params={"q": "x", "limit": limit}).status_code == 422
    assert recorder.calls == []


def test_search_missing_q_is_422(client):
    assert client.get("/sources/youtube/search").status_code == 422


# ── clean ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def check_calls(monkeypatch):
    calls: list[str] = []

    async def fake_check(video_id: str, **_kwargs):
        calls.append(video_id)
        return source_check.CleanCheck(clean=True, confidence=0.9, reason="no captions", frames_checked=4)

    monkeypatch.setattr(source_check, "check_clean", fake_check)
    return calls


def test_clean_returns_the_check(client, check_calls):
    response = client.get(f"/sources/youtube/{ID_A}/clean")

    assert response.status_code == 200
    assert response.json() == {"clean": True, "confidence": 0.9, "reason": "no captions", "frames_checked": 4}
    assert check_calls == [ID_A]


@pytest.mark.parametrize(
    "video_id",
    ["..%2Fx", "a%20b", "--help", "abcdefghijkl", "abcdefghij", "a;b&c|d$e", "-" * 11 + "x"],
)
def test_clean_refuses_an_id_that_is_not_eleven_id_characters(client, check_calls, video_id):
    response = client.get(f"/sources/youtube/{video_id}/clean")

    assert response.status_code == 400
    assert check_calls == []


@pytest.mark.parametrize("video_id", ["%0Aabcdefghij", "abcdefghij%0A", "abcdefghijk%0Aabcdefghijk"])
def test_clean_never_reaches_the_check_with_a_newline_in_the_id(client, check_calls, video_id):
    # The route pattern does not match a newline, so this is a 404, never a check.
    assert client.get(f"/sources/youtube/{video_id}/clean").status_code in (400, 404)
    assert check_calls == []


def test_the_id_pattern_refuses_a_trailing_newline():
    # ``$`` alone would let "abcdefghijk\n" through ``re.match``; the router uses ``fullmatch``.
    assert source_check.VIDEO_ID_RE.fullmatch("abcdefghijk\n") is None
    assert source_check.VIDEO_ID_RE.fullmatch("abcdefghijk") is not None


def test_clean_accepts_ids_with_dash_and_underscore(client, check_calls):
    assert client.get("/sources/youtube/-_-_-_-_-_-/clean").status_code == 200
    assert check_calls == ["-_-_-_-_-_-"]


def test_clean_answers_429_when_too_many_checks_run(client, monkeypatch):
    async def busy(video_id: str, **_kwargs):
        raise source_check.CheckBusyError("busy")

    monkeypatch.setattr(source_check, "check_clean", busy)

    assert client.get(f"/sources/youtube/{ID_A}/clean").status_code == 429


def test_clean_unavailable_is_a_200_with_null(client, monkeypatch):
    async def unavailable(video_id: str, **_kwargs):
        return source_check.UNAVAILABLE

    monkeypatch.setattr(source_check, "check_clean", unavailable)

    response = client.get(f"/sources/youtube/{ID_A}/clean")

    assert response.status_code == 200
    assert response.json() == {"clean": None, "confidence": 0.0, "reason": "check unavailable", "frames_checked": 0}
