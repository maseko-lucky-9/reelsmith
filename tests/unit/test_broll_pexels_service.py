"""Pexels B-roll provider (FR-010, T012): ``broll_pexels_service.fetch_asset``.

Every request goes through ``httpx.MockTransport``; nothing here touches the
network. The response shape follows the Pexels API documentation
(https://www.pexels.com/api/documentation/#videos-search): ``videos[]`` with
``id``, ``url`` (the video's Pexels page), ``user.name`` and ``video_files[]``
(``file_type``, ``width``, ``height``, ``link``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

from app.services import broll_pexels_service as svc
from app.services.broll_service import BrollAsset
from app.settings import settings

KEY = "PLACEHOLDER-NOT-A-REAL-KEY"
SEARCH = "https://api.pexels.com/videos/search"
VIDEO_HOST = "https://videos.pexels.com/video-files/1234"


def _video(video_id=1234, files=None, **extra):
    return {
        "id": video_id,
        "width": 1080,
        "height": 1920,
        "url": f"https://www.pexels.com/video/ocean-waves-{video_id}/",
        "duration": 8,
        "user": {"id": 1, "name": "Jane Doe", "url": "https://www.pexels.com/@jane"},
        "video_files": files
        if files is not None
        else [
            {"id": 1, "quality": "uhd", "file_type": "video/mp4", "width": 2160, "height": 3840,
             "link": f"{VIDEO_HOST}/uhd_2160_3840.mp4"},
            {"id": 2, "quality": "hd", "file_type": "video/mp4", "width": 1080, "height": 1920,
             "link": f"{VIDEO_HOST}/hd_1080_1920.mp4"},
            {"id": 3, "quality": "sd", "file_type": "video/mp4", "width": 540, "height": 960,
             "link": f"{VIDEO_HOST}/sd_540_960.mp4"},
            {"id": 4, "quality": "hd", "file_type": "video/mp4", "width": 1920, "height": 1080,
             "link": f"{VIDEO_HOST}/hd_1920_1080.mp4"},
            {"id": 5, "quality": "hls", "file_type": "video/mp4", "width": None, "height": None,
             "link": f"{VIDEO_HOST}/playlist.m3u8"},
        ],
        **extra,
    }  # fmt: skip


class _Server:
    """A scripted Pexels: one search answer, one download answer."""

    def __init__(self, *, search=None, download=None):
        self.requests: list[httpx.Request] = []
        self.search = (
            search
            if search is not None
            else httpx.Response(
                200, json={"page": 1, "per_page": 5, "videos": [_video()]}
            )
        )
        self.download = (
            download
            if download is not None
            else httpx.Response(
                200, content=b"video-bytes", headers={"content-type": "video/mp4"}
            )
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url).startswith(SEARCH):
            return self.search
        return self.download

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(settings, "pexels_api_key", KEY)
    return KEY


@pytest.fixture
def cache(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "cache"
    monkeypatch.setattr(settings, "broll_cache_dir", str(root))
    return root


async def _fetch(server: _Server, query="ocean", **kwargs):
    async with server.client() as client:
        return await svc.fetch_asset(query, client=client, **kwargs)


# ── success ───────────────────────────────────────────────────────────────────


async def test_search_then_download_returns_the_asset(key, cache):
    server = _Server()

    asset = await _fetch(server)

    path = cache / "pexels" / "videos" / "1234.mp4"
    assert asset == BrollAsset(
        path=str(path),
        provider="pexels",
        asset_id="1234",
        author="Jane Doe",
        source_url="https://www.pexels.com/video/ocean-waves-1234/",
    )
    assert path.read_bytes() == b"video-bytes"
    assert server.urls()[1] == f"{VIDEO_HOST}/hd_1080_1920.mp4"


async def test_the_search_request(key, cache):
    server = _Server()

    await _fetch(server, "  Ocean  ")

    search = server.requests[0]
    assert search.method == "GET"
    assert search.url.copy_with(query=None) == httpx.URL(SEARCH)
    assert search.url.params["query"] == "ocean"
    assert search.url.params["orientation"] == "portrait"
    assert search.headers["Authorization"] == KEY


async def test_the_key_never_goes_to_the_download_host(key, cache):
    server = _Server()

    await _fetch(server)

    assert "authorization" not in server.requests[1].headers


async def test_the_widest_portrait_mp4_up_to_1080_wide_is_chosen(key, cache):
    files = [
        {
            "file_type": "video/mp4",
            "width": 720,
            "height": 1280,
            "link": f"{VIDEO_HOST}/a.mp4",
        },
        {
            "file_type": "video/mp4",
            "width": 1080,
            "height": 1080,
            "link": f"{VIDEO_HOST}/sq.mp4",
        },
        {
            "file_type": "video/mp4",
            "width": 960,
            "height": 540,
            "link": f"{VIDEO_HOST}/land.mp4",
        },
        {
            "file_type": "video/webm",
            "width": 1080,
            "height": 1920,
            "link": f"{VIDEO_HOST}/w.webm",
        },
        {
            "file_type": "video/mp4",
            "width": 1200,
            "height": 2000,
            "link": f"{VIDEO_HOST}/big.mp4",
        },
    ]
    server = _Server(search=httpx.Response(200, json={"videos": [_video(files=files)]}))

    await _fetch(server)

    assert server.urls()[1] == f"{VIDEO_HOST}/a.mp4"


async def test_without_a_portrait_file_the_widest_other_one_is_chosen(key, cache):
    files = [
        {"file_type": "video/mp4", "width": 1080, "height": 1080, "link": f"{VIDEO_HOST}/sq.mp4"},
        {"file_type": "video/mp4", "width": 960, "height": 540, "link": f"{VIDEO_HOST}/land.mp4"},
    ]
    server = _Server(search=httpx.Response(200, json={"videos": [_video(files=files)]}))

    await _fetch(server)

    assert server.urls()[1] == f"{VIDEO_HOST}/sq.mp4"


async def test_a_landscape_file_is_used_when_no_portrait_one_fits(key, cache):
    files = [
        {
            "file_type": "video/mp4",
            "width": 640,
            "height": 360,
            "link": f"{VIDEO_HOST}/s.mp4",
        },
        {
            "file_type": "video/mp4",
            "width": 960,
            "height": 540,
            "link": f"{VIDEO_HOST}/m.mp4",
        },
    ]
    server = _Server(search=httpx.Response(200, json={"videos": [_video(files=files)]}))

    await _fetch(server)

    assert server.urls()[1] == f"{VIDEO_HOST}/m.mp4"


async def test_a_video_without_a_usable_file_is_passed_over(key, cache):
    too_big = [
        {
            "file_type": "video/mp4",
            "width": 2160,
            "height": 3840,
            "link": f"{VIDEO_HOST}/u.mp4",
        }
    ]
    server = _Server(
        search=httpx.Response(
            200, json={"videos": [_video(1, files=too_big), _video(2)]}
        )
    )

    asset = await _fetch(server)

    assert asset is not None and asset.asset_id == "2"


@pytest.mark.parametrize(
    "body",
    [{"videos": []}, {"videos": [_video(files=[])]}, {}, {"videos": "nope"}],
)
async def test_no_usable_result_is_none(key, cache, body):
    server = _Server(search=httpx.Response(200, json=body))

    assert await _fetch(server) is None
    assert len(server.requests) == 1


# ── cache ─────────────────────────────────────────────────────────────────────


async def test_a_cached_query_needs_no_network(key, cache):
    await _fetch(_Server())
    offline = _Server(search=httpx.Response(500), download=httpx.Response(500))

    asset = await _fetch(offline)

    assert offline.requests == []
    assert asset is not None and asset.asset_id == "1234" and asset.author == "Jane Doe"


async def test_a_cached_video_is_not_downloaded_again(key, cache):
    await _fetch(_Server(), "ocean")
    server = _Server()

    asset = await _fetch(server, "sea")

    assert server.urls() == [server.urls()[0]]  # the search only
    assert server.urls()[0].startswith(SEARCH)
    assert asset is not None and asset.asset_id == "1234"


async def test_a_query_whose_video_file_vanished_downloads_again(key, cache):
    asset = await _fetch(_Server())
    Path(asset.path).unlink()
    server = _Server()

    again = await _fetch(server)

    assert len(server.requests) == 2
    assert Path(again.path).read_bytes() == b"video-bytes"


async def test_the_cache_dir_is_read_at_call_time(key, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "broll_cache_dir", str(tmp_path / "elsewhere"))

    asset = await _fetch(_Server())

    assert Path(asset.path).parent == tmp_path / "elsewhere" / "pexels" / "videos"


async def test_cache_files_are_written_atomically(key, cache):
    await _fetch(_Server())

    leftovers = [p.name for p in cache.rglob("*") if p.is_file() and ".part" in p.name]
    assert leftovers == []
    meta = json.loads((cache / "pexels" / "videos" / "1234.json").read_text())
    assert meta["author"] == "Jane Doe"


# ── failures: None, logged, the key never in the log ──────────────────────────


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_a_failed_search_is_none_and_logged(key, cache, caplog, status):
    server = _Server(search=httpx.Response(status, json={"error": "nope"}))

    with caplog.at_level(logging.DEBUG):
        assert await _fetch(server) is None

    assert f"HTTP {status}" in caplog.text
    assert KEY not in caplog.text
    assert len(server.requests) == 1


@pytest.mark.parametrize("status", [403, 404, 500])
async def test_a_failed_download_is_none_and_logged(key, cache, caplog, status):
    server = _Server(download=httpx.Response(status))

    with caplog.at_level(logging.DEBUG):
        assert await _fetch(server) is None

    assert f"HTTP {status}" in caplog.text
    assert KEY not in caplog.text
    assert list((cache / "pexels" / "videos").glob("*.mp4")) == []


async def test_a_network_error_is_none_and_logged(key, cache, caplog):
    def _boom(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    with caplog.at_level(logging.DEBUG):
        async with httpx.AsyncClient(transport=httpx.MockTransport(_boom)) as client:
            assert await svc.fetch_asset("ocean", client=client) is None

    assert "timed out" in caplog.text
    assert KEY not in caplog.text


async def test_a_bad_json_body_is_none(key, cache, caplog):
    server = _Server(search=httpx.Response(200, content=b"<html>"))

    with caplog.at_level(logging.DEBUG):
        assert await _fetch(server) is None
    assert KEY not in caplog.text


async def test_the_success_path_never_logs_the_key(key, cache, caplog):
    with caplog.at_level(logging.DEBUG):
        assert await _fetch(_Server()) is not None

    assert KEY not in caplog.text


@pytest.mark.parametrize("missing", [None, ""])
async def test_without_a_key_nothing_is_requested(cache, monkeypatch, caplog, missing):
    monkeypatch.setattr(settings, "pexels_api_key", missing)
    server = _Server()

    with caplog.at_level(logging.INFO):
        assert await _fetch(server) is None

    assert server.requests == []
    assert "YTVIDEO_PEXELS_API_KEY" in caplog.text


async def test_the_key_is_read_at_call_time(cache, monkeypatch):
    monkeypatch.setattr(settings, "pexels_api_key", "first")
    server = _Server(search=httpx.Response(401))
    await _fetch(server)
    monkeypatch.setattr(settings, "pexels_api_key", "second")
    await _fetch(server)

    assert [r.headers["Authorization"] for r in server.requests] == ["first", "second"]


@pytest.mark.parametrize("query", ["", "   "])
async def test_an_empty_query_requests_nothing(key, cache, query):
    server = _Server()

    assert await _fetch(server, query) is None
    assert server.requests == []


# ── download limits ───────────────────────────────────────────────────────────


async def test_a_declared_oversize_body_is_refused_before_reading(key, cache, caplog):
    # The body itself would fit: only the declared length can refuse it.
    server = _Server(
        download=httpx.Response(
            200, content=b"x" * 16, headers={"content-length": "999999"}
        )
    )

    with caplog.at_level(logging.WARNING):
        assert await _fetch(server, max_bytes=32) is None

    assert "too large" in caplog.text
    assert list(cache.rglob("*.mp4")) == []


async def test_an_oversize_stream_is_aborted_while_streaming(key, cache, caplog):
    chunks_sent = []

    async def _stream():
        for _ in range(100):
            chunks_sent.append(1)
            yield b"x" * 10

    server = _Server(download=httpx.Response(200, content=_stream()))

    with caplog.at_level(logging.WARNING):
        assert await _fetch(server, max_bytes=55) is None

    assert len(chunks_sent) < 100  # stopped reading
    assert "too large" in caplog.text
    assert [p for p in cache.rglob("*") if p.is_file() and p.suffix != ".json"] == []


async def test_a_body_at_the_limit_is_kept(key, cache):
    server = _Server(download=httpx.Response(200, content=b"x" * 32))

    asset = await _fetch(server, max_bytes=32)

    assert Path(asset.path).stat().st_size == 32


def test_the_default_size_limit_is_50_mb():
    assert svc.MAX_DOWNLOAD_BYTES == 50 * 1024 * 1024


# ── SSRF guard: downloads only from Pexels hosts ──────────────────────────────


@pytest.mark.parametrize(
    "link",
    [
        "https://player.vimeo.com/external/342571552.hd.mp4",
        "https://evilpexels.com/x.mp4",
        "https://videos.pexels.com.evil.test/x.mp4",
        "http://videos.pexels.com/video-files/1/x.mp4",
        "https://videos.pexels.com:8443/video-files/1/x.mp4",
        "https://videos.pexels.com@169.254.169.254/latest/meta-data",
        "https://169.254.169.254/x.mp4",
        "file:///etc/passwd",
        "not a url",
    ],
)
async def test_a_link_off_the_pexels_hosts_is_never_requested(key, cache, caplog, link):
    files = [{"file_type": "video/mp4", "width": 720, "height": 1280, "link": link}]
    server = _Server(search=httpx.Response(200, json={"videos": [_video(files=files)]}))

    with caplog.at_level(logging.WARNING):
        assert await _fetch(server) is None

    assert len(server.requests) == 1  # the search only
    assert "not an allowed Pexels download" in caplog.text


@pytest.mark.parametrize(
    "link",
    [
        "https://videos.pexels.com/video-files/1234/x.mp4",
        "https://www.pexels.com/download/video/1234/",
        "https://pexels.com/x.mp4",
    ],
)
async def test_links_on_pexels_hosts_are_downloaded(key, cache, link):
    files = [{"file_type": "video/mp4", "width": 720, "height": 1280, "link": link}]
    server = _Server(search=httpx.Response(200, json={"videos": [_video(files=files)]}))

    assert await _fetch(server) is not None
    assert server.urls()[1] == link


async def test_a_redirect_is_not_followed(key, cache):
    server = _Server(
        download=httpx.Response(302, headers={"location": "https://169.254.169.254/x"})
    )

    assert await _fetch(server) is None
    assert len(server.requests) == 2


@pytest.mark.parametrize("video_id", ["../../etc", "12a", None, -5, 1.5])
async def test_a_video_id_that_is_not_a_number_is_rejected(key, cache, video_id):
    server = _Server(search=httpx.Response(200, json={"videos": [_video(video_id)]}))

    assert await _fetch(server) is None
    assert len(server.requests) == 1


async def test_a_credit_link_off_pexels_falls_back_to_the_video_page(key, cache):
    video = _video()
    video["url"] = "javascript:alert(1)"
    server = _Server(search=httpx.Response(200, json={"videos": [video]}))

    asset = await _fetch(server)

    assert asset.source_url == "https://www.pexels.com/video/1234/"
