"""Tests for the GET /jobs/preview/thumbnail proxy endpoint."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a TestClient for the FastAPI app with the jobs router."""
    from fastapi import FastAPI
    from app.routers.jobs import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_thumbnail_proxy_success(client, monkeypatch):
    """Successful thumbnail proxy fetches yt-dlp metadata and proxies the image."""
    # Mock subprocess.run to return a fake yt-dlp response
    fake_info = {"thumbnail": "https://img.youtube.com/vi/abc/hqdefault.jpg"}

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = json.dumps(fake_info)
        return result

    monkeypatch.setattr("app.routers.jobs.subprocess.run", fake_run)

    # Mock httpx.AsyncClient
    fake_response = MagicMock()
    fake_response.content = b"\xff\xd8\xff\xe0"  # JPEG magic bytes
    fake_response.headers = {"content-type": "image/jpeg"}
    fake_response.raise_for_status = MagicMock()

    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(return_value=fake_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr("app.routers.jobs.httpx.AsyncClient", lambda **kwargs: mock_client_instance)

    resp = client.get("/jobs/preview/thumbnail", params={"url": "https://www.youtube.com/watch?v=abc"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == "public, max-age=3600"
    assert resp.content == b"\xff\xd8\xff\xe0"


def test_thumbnail_proxy_no_thumbnail(client, monkeypatch):
    """Returns 404 when yt-dlp has no thumbnail in metadata."""
    fake_info = {"title": "No Thumb"}

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = json.dumps(fake_info)
        return result

    monkeypatch.setattr("app.routers.jobs.subprocess.run", fake_run)

    resp = client.get("/jobs/preview/thumbnail", params={"url": "https://www.youtube.com/watch?v=abc"})
    assert resp.status_code == 404


def test_thumbnail_proxy_ytdlp_failure(client, monkeypatch):
    """Returns 404 when yt-dlp fails."""
    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        return result

    monkeypatch.setattr("app.routers.jobs.subprocess.run", fake_run)

    resp = client.get("/jobs/preview/thumbnail", params={"url": "https://bad.url/video"})
    assert resp.status_code == 404
