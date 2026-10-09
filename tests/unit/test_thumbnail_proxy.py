"""GET /jobs/preview and GET /jobs/preview/thumbnail.

Both read yt-dlp metadata through ``app.services.yt_dlp_metadata``, which runs
``python -m yt_dlp`` as an async child process. The tests replace that child
process (``create_subprocess_exec``) — no network, no real yt-dlp.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.routers import jobs as jobs_router
from app.services import yt_dlp_metadata

URL = "https://www.youtube.com/watch?v=abc"
_HANG_SECONDS = 8.0


@pytest.fixture
def client():
    """Create a TestClient for the FastAPI app with the jobs router."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(jobs_router.router)
    return TestClient(app)


class FakeProc:
    """Stand-in for ``asyncio.subprocess.Process``.

    ``hang=True`` makes ``communicate`` block like a yt-dlp stuck on a dead
    socket. The hang ends by itself after ``_HANG_SECONDS`` (then yields no
    output) so a broken timeout fails a test instead of hanging the run.
    """

    def __init__(self, stdout: bytes = b"", returncode: int = 0, hang: bool = False):
        self._stdout = stdout
        self._final_rc = returncode
        self._hang = hang
        self.returncode: int | None = None
        self.killed = False
        self.waited = False

    async def communicate(self, input=None):
        if self._hang:
            await asyncio.sleep(_HANG_SECONDS)
        self.returncode = self._final_rc
        return self._stdout, b""

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        self.waited = True
        return self.returncode


def _install(monkeypatch, proc: FakeProc) -> list:
    calls: list = []

    async def fake_exec(*argv, **kwargs):
        calls.append(argv)
        return proc

    monkeypatch.setattr(yt_dlp_metadata, "create_subprocess_exec", fake_exec)
    return calls


def _ok(info: dict) -> FakeProc:
    return FakeProc(json.dumps(info).encode())


def _mock_http(monkeypatch, content: bytes, content_type: str = "image/jpeg"):
    fake_response = MagicMock()
    fake_response.content = content
    fake_response.headers = {"content-type": content_type}
    fake_response.raise_for_status = MagicMock()

    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(return_value=fake_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "app.routers.jobs.httpx.AsyncClient", lambda **kwargs: mock_client_instance
    )
    return mock_client_instance


# ── /jobs/preview/thumbnail ───────────────────────────────────────────────────


def test_thumbnail_proxy_success(client, monkeypatch):
    """Successful thumbnail proxy reads yt-dlp metadata and proxies the image."""
    calls = _install(
        monkeypatch, _ok({"thumbnail": "https://img.youtube.com/vi/abc/hqdefault.jpg"})
    )
    http = _mock_http(monkeypatch, b"\xff\xd8\xff\xe0")  # JPEG magic bytes

    resp = client.get("/jobs/preview/thumbnail", params={"url": URL})

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == "public, max-age=3600"
    assert resp.content == b"\xff\xd8\xff\xe0"
    assert calls[0][:3] == (sys.executable, "-m", "yt_dlp")
    http.get.assert_awaited_once_with(
        "https://img.youtube.com/vi/abc/hqdefault.jpg",
        headers={"Referer": "https://www.youtube.com/"},
    )


def test_thumbnail_proxy_no_thumbnail(client, monkeypatch):
    """Returns 404 when yt-dlp has no thumbnail in metadata."""
    _install(monkeypatch, _ok({"title": "No Thumb"}))

    resp = client.get("/jobs/preview/thumbnail", params={"url": URL})

    assert resp.status_code == 404
    assert resp.json() == {"detail": "No thumbnail available"}


def test_thumbnail_proxy_ytdlp_failure(client, monkeypatch):
    """Returns 404 when yt-dlp fails."""
    _install(monkeypatch, FakeProc(b"", returncode=1))

    resp = client.get(
        "/jobs/preview/thumbnail", params={"url": "https://bad.url/video"}
    )

    assert resp.status_code == 404
    assert resp.json() == {"detail": "No thumbnail available"}


def test_thumbnail_proxy_timeout_is_404_and_kills_the_child(client, monkeypatch):
    proc = FakeProc(hang=True)
    _install(monkeypatch, proc)
    monkeypatch.setattr(jobs_router, "_YT_DLP_TIMEOUT_SECONDS", 0.2)

    started = time.monotonic()
    resp = client.get("/jobs/preview/thumbnail", params={"url": URL})
    elapsed = time.monotonic() - started

    assert resp.status_code == 404
    assert resp.json() == {"detail": "Could not resolve thumbnail"}
    assert elapsed < 4, f"timeout not enforced ({elapsed:.1f}s)"
    assert proc.killed and proc.waited


# ── /jobs/preview ─────────────────────────────────────────────────────────────


def test_preview_returns_metadata(client, monkeypatch):
    calls = _install(
        monkeypatch,
        _ok(
            {
                "title": "Clip",
                "duration": 61,
                "height": 1080,
                "thumbnail": "https://t/x.jpg",
            }
        ),
    )

    resp = client.get("/jobs/preview", params={"url": URL})

    assert resp.status_code == 200
    assert resp.json() == {
        "title": "Clip",
        "duration": 61.0,
        "resolution": "1080p",
        "thumbnail": "https://t/x.jpg",
    }
    argv = calls[0]
    assert argv[:3] == (sys.executable, "-m", "yt_dlp")
    assert argv[-2:] == ("--", URL)


def test_preview_failure_returns_empty_fields(client, monkeypatch):
    _install(monkeypatch, FakeProc(b"ERROR", returncode=1))

    resp = client.get("/jobs/preview", params={"url": URL})

    assert resp.status_code == 200
    assert resp.json() == {
        "title": "",
        "duration": 0.0,
        "resolution": "",
        "thumbnail": "",
    }


async def test_preview_bounded_and_does_not_hold_executor(monkeypatch):
    """A hung yt-dlp is killed at the timeout, the route answers with its
    existing empty-fields fallback, and the default executor stays free while
    the child hangs (the lifespan sizes it at a few workers)."""
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
    proc = FakeProc(hang=True)
    _install(monkeypatch, proc)
    monkeypatch.setattr(jobs_router, "_YT_DLP_TIMEOUT_SECONDS", 1.0)

    preview = asyncio.create_task(jobs_router.preview_video(URL))
    await asyncio.sleep(0.1)
    assert not preview.done()

    # The only executor worker must be free while yt-dlp hangs.
    assert (
        await asyncio.wait_for(loop.run_in_executor(None, lambda: "free"), 0.5)
        == "free"
    )

    result = await asyncio.wait_for(preview, timeout=5)

    assert result.model_dump() == {
        "title": "",
        "duration": 0.0,
        "resolution": "",
        "thumbnail": "",
    }
    assert proc.killed, "hung yt-dlp was not killed"
    assert proc.waited, "killed yt-dlp was not reaped"
