"""Unit tests for broll_pexels_service (W1.9).

httpx.MockTransport for the network layer; tmp_path for the cache.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.services import broll_pexels_service as svc


_VIDEO_FIXTURE = {
    "id": 1234,
    "url": "https://www.pexels.com/video/1234/",
    "duration": 8,
    "user": {"name": "Jane Doe", "url": "https://pexels.com/jane"},
    "video_files": [
        {"link": "https://cdn/1080.mp4", "file_type": "video/mp4", "height": 1080, "width": 1920},
        {"link": "https://cdn/720.mp4",  "file_type": "video/mp4", "height": 720,  "width": 1280},
        {"link": "https://cdn/360.mp4",  "file_type": "video/mp4", "height": 360,  "width": 640},
    ],
}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_writes_cache(tmp_path):
    calls: list[httpx.Request] = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"videos": [_VIDEO_FIXTURE]})

    with _client(handler) as http:
        body = svc.search("sunset", "KEY", cache_dir=tmp_path, http=http)
    assert body["videos"][0]["id"] == 1234

    files = list((tmp_path / "search").iterdir())
    assert len(files) == 1
    cached = json.loads(files[0].read_text())
    assert cached["_cached_at"] > 0
    assert calls[0].headers["authorization"] == "KEY"


def test_search_uses_cache_on_repeat(tmp_path):
    n_calls = 0
    def handler(req):
        nonlocal n_calls
        n_calls += 1
        return httpx.Response(200, json={"videos": [_VIDEO_FIXTURE]})

    with _client(handler) as http:
        svc.search("sunset", "KEY", cache_dir=tmp_path, http=http)
        svc.search("sunset", "KEY", cache_dir=tmp_path, http=http)
    assert n_calls == 1


def test_search_empty_query_returns_no_videos(tmp_path):
    out = svc.search("   ", "KEY", cache_dir=tmp_path, http=None)
    assert out == {"videos": []}


def test_pick_smallest_video_file():
    smallest = svc._pick_smallest_video_file(_VIDEO_FIXTURE)
    assert smallest["height"] == 360


def test_fetch_asset_downloads_and_returns_dict(tmp_path):
    def handler(req):
        if "search" in req.url.path:
            return httpx.Response(200, json={"videos": [_VIDEO_FIXTURE]})
        if req.url.path == "/360.mp4":
            return httpx.Response(200, content=b"video-bytes")
        return httpx.Response(404)

    with _client(handler) as http:
        out = svc.fetch_asset("sunset", "KEY", cache_dir=tmp_path, http=http)
    assert out is not None
    assert out["source"] == "pexels"
    assert out["asset_id"] == "1234"
    assert out["photographer"] == "Jane Doe"
    assert (tmp_path / "videos" / "1234.mp4").read_bytes() == b"video-bytes"


def test_fetch_asset_no_results_returns_none(tmp_path):
    def handler(req):
        return httpx.Response(200, json={"videos": []})

    with _client(handler) as http:
        out = svc.fetch_asset("noresults", "KEY", cache_dir=tmp_path, http=http)
    assert out is None


def test_fetch_asset_skips_redownload_when_cached(tmp_path):
    download_calls = 0
    def handler(req):
        nonlocal download_calls
        if "search" in req.url.path:
            return httpx.Response(200, json={"videos": [_VIDEO_FIXTURE]})
        download_calls += 1
        return httpx.Response(200, content=b"x")

    with _client(handler) as http:
        svc.fetch_asset("sunset", "KEY", cache_dir=tmp_path, http=http)
        svc.fetch_asset("sunset", "KEY", cache_dir=tmp_path, http=http)
    assert download_calls == 1


def test_search_http_error_raises(tmp_path):
    def handler(req):
        return httpx.Response(500)

    with _client(handler) as http:
        with pytest.raises(svc.PexelsError):
            svc.search("sunset", "KEY", cache_dir=tmp_path, http=http)
