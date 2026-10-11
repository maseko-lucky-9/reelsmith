"""One route scheme for dev and ``serve_frontend`` (T034).

Every router is unprefixed, and ``ApiPrefixMiddleware`` strips a leading
``/api`` segment, so the API answers at both ``/x`` and ``/api/x``: the Vite
dev proxy (which strips ``/api``) and the built UI served by FastAPI (which
does not) reach the same routes.

Every test that can reach a route using ``get_session`` overrides it with an
in-memory SQLite engine, so nothing touches the developer's database.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.main as app_main
from app.db.base import Base
from app.db.models import ClipRecord, JobRecord
from app.db.session import get_session
from app.domain.events import Event, EventType
from app.domain.models import JobState
from app.main import create_app
from app.settings import settings

GENERIC_404 = {"detail": "Not Found"}


@asynccontextmanager
async def _memory_db(app) -> AsyncIterator[str]:
    """Point ``get_session`` at a fresh in-memory SQLite with one unrendered
    clip; yields that clip's id."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        job = JobRecord(youtube_url="https://example.com/v")
        session.add(job)
        await session.flush()
        clip = ClipRecord(job_id=job.id, start=0.0, end=10.0)
        session.add(clip)
        await session.commit()
        clip_id = clip.id

    async def _override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override
    try:
        yield clip_id
    finally:
        await engine.dispose()


@pytest.fixture
async def api(tmp_path) -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, str]]]:
    app = create_app()
    async with _memory_db(app) as clip_id, LifespanManager(app):
        await app.state.job_store.create(
            JobState(
                job_id="job-parity",
                url="https://www.youtube.com/watch?v=parity00000",
                download_path=str(tmp_path),
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, {"clip_id": clip_id, "tmp": str(tmp_path)}


# (method, path, request kwargs). ``{clip_id}`` and ``{tmp}`` are filled in
# from the fixture. One or more routes per router family, including the six
# routers that used to declare ``prefix="/api/..."``.
PARITY_CASES: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/health", {}),
    # jobs
    ("GET", "/jobs", {}),
    ("GET", "/jobs/job-parity", {}),
    ("GET", "/jobs/missing", {}),
    ("GET", "/jobs/preview", {}),
    # clips + media
    ("GET", "/clips", {}),
    ("PATCH", "/clips/missing/like", {}),
    ("GET", "/clips/missing/video", {}),
    # social, brand templates, folders, uploads (multipart)
    ("GET", "/social/accounts", {}),
    ("GET", "/brand-templates", {}),
    (
        "POST",
        "/folders",
        {"json": {"download_path": "{tmp}", "url": "upload://parity"}},
    ),
    ("POST", "/uploads", {"files": {"file": ("notes.txt", b"data", "text/plain")}}),
    # formerly /api-prefixed routers
    ("GET", "/clips/{clip_id}/edit", {}),
    ("GET", "/clips/missing/edit", {}),
    ("GET", "/clips/{clip_id}/export.xml", {}),
    ("POST", "/clips/missing/ai-hook", {}),
    ("POST", "/clips/{clip_id}/enhance-speech", {}),
    ("POST", "/clips/missing/enhance-speech", {}),
    ("POST", "/jobs/missing/reprompt", {"json": {"prompt": "x"}}),
    ("GET", "/clips/bulk-export.zip", {"params": {"ids": "not-real"}}),
    ("GET", "/clips/bulk-export.zip", {}),
]


def _fill(value: Any, names: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(**names)
    if isinstance(value, dict):
        return {k: _fill(v, names) for k, v in value.items()}
    return value


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    PARITY_CASES,
    ids=[f"{m} {p}{' ' + ','.join(k) if k else ''}" for m, p, k in PARITY_CASES],
)
async def test_route_answers_the_same_with_and_without_api_prefix(
    api, method, path, kwargs
):
    client, names = api
    path = path.format(**names)
    kwargs = _fill(kwargs, names)

    bare = await client.request(method, path, **kwargs)
    prefixed = await client.request(method, f"/api{path}", **kwargs)

    assert (prefixed.status_code, prefixed.json()) == (bare.status_code, bare.json())
    # Reached the route, not the router's fall-through 404.
    assert bare.json() != GENERIC_404


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
async def test_docs_routes_answer_with_and_without_api_prefix(api, path):
    client, _ = api

    bare = await client.get(path)
    prefixed = await client.get(f"/api{path}")

    assert bare.status_code == prefixed.status_code == 200
    assert prefixed.content == bare.content


@pytest.mark.parametrize("path", ["/apixyz", "/apijobs", "/apihealth", "/api-health"])
async def test_paths_that_only_start_with_api_are_not_rewritten(api, path):
    client, _ = api

    response = await client.get(path)

    assert response.status_code == 404
    assert response.json() == GENERIC_404


def test_openapi_has_no_api_prefixed_paths():
    paths = create_app().openapi()["paths"]

    prefixed = [p for p in paths if p == "/api" or p.startswith("/api/")]
    assert prefixed == []
    # The six formerly /api-prefixed routers are present unprefixed.
    for path in (
        "/jobs/{job_id}/reprompt",
        "/clips/{clip_id}/edit",
        "/clips/{clip_id}/ai-hook",
        "/clips/{clip_id}/export.xml",
        "/clips/{clip_id}/enhance-speech",
        "/clips/bulk-export.zip",
    ):
        assert path in paths, path


# ── SSE through the middleware ───────────────────────────────────────────────


@pytest.mark.parametrize("prefix", ["", "/api"])
async def test_job_events_stream_before_the_response_ends(prefix):
    """Drive the ASGI app directly: the first SSE event must reach the client
    while the stream is still open (the stream only ends on disconnect, so a
    buffering middleware would never deliver it)."""
    app = create_app()
    async with LifespanManager(app):
        await app.state.job_store.create(
            JobState(
                job_id="job-sse", url="https://example.com/v", download_path="/tmp"
            )
        )
        # Published before the subscription: the bus replays it to the stream.
        await app.state.event_bus.publish(
            Event(type=EventType.FOLDER_CREATED, job_id="job-sse")
        )
        first_event = asyncio.Event()
        sent: list[dict[str, Any]] = []
        request_delivered = False

        async def receive() -> dict[str, Any]:
            nonlocal request_delivered
            if not request_delivered:
                request_delivered = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await first_event.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)
            if message["type"] == "http.response.body" and b"event:" in message.get(
                "body", b""
            ):
                first_event.set()

        path = f"{prefix}/jobs/job-sse/events"
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"test")],
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
        }

        await asyncio.wait_for(app(scope, receive, send), timeout=5)

    start = sent[0]
    assert start["status"] == 200
    assert dict(start["headers"])[b"content-type"].startswith(b"text/event-stream")
    [first_body] = [
        m
        for m in sent
        if m["type"] == "http.response.body" and b"event:" in m.get("body", b"")
    ][:1]
    assert b"event: FolderCreated" in first_body["body"]
    assert first_body.get("more_body") is True


# ── serve_frontend: SPA at "/" and the API under /api ────────────────────────


@asynccontextmanager
async def _serve_frontend(tmp_path, monkeypatch) -> AsyncIterator[httpx.AsyncClient]:
    """App built with ``serve_frontend`` on and a temporary ``web/dist``."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>reelsmith-spa</title>")
    (dist / "assets" / "app.js").write_text("console.log('spa')")
    monkeypatch.setattr(settings, "serve_frontend", True)
    monkeypatch.setattr(app_main, "FRONTEND_DIST", dist)

    app = create_app()
    async with _memory_db(app), LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


@pytest.fixture
async def served(tmp_path, monkeypatch) -> AsyncIterator[httpx.AsyncClient]:
    async with _serve_frontend(tmp_path, monkeypatch) as client:
        yield client


async def test_serve_frontend_serves_the_spa_at_root(served):
    index = await served.get("/")
    asset = await served.get("/assets/app.js")

    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert "reelsmith-spa" in index.text
    assert asset.status_code == 200
    assert asset.text == "console.log('spa')"


@pytest.mark.parametrize("path", ["/api/health", "/health"])
async def test_serve_frontend_health_is_json(served, path):
    response = await served.get(path)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "job_store": "memory"}


async def test_serve_frontend_reaches_api_routes_under_api(served):
    jobs = await served.get("/api/jobs")
    clips = await served.get("/api/clips")
    hook = await served.post("/api/clips/missing/ai-hook")
    zip_ = await served.get("/api/clips/bulk-export.zip", params={"ids": "not-real"})

    assert (jobs.status_code, jobs.json()) == (200, [])
    assert (clips.status_code, clips.json()) == (200, [])
    assert (hook.status_code, hook.json()) == (404, {"detail": "clip not found"})
    assert (zip_.status_code, zip_.json()) == (404, {"detail": "no clips matched"})


async def test_serve_frontend_unknown_paths_fall_through_to_static_404(served):
    for path in ("/apixyz", "/api/no-such-route", "/no-such-file.js"):
        response = await served.get(path)
        assert response.status_code == 404, path


async def test_serve_frontend_with_auth_keeps_the_spa_open_and_the_api_closed(
    tmp_path, monkeypatch
):
    """FR-060: the static mount is not an API route, so the app-level API-key
    dependency does not cover it; every API address does."""
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "api_key", "test-key")
    key = {"Authorization": "Bearer test-key"}

    async with _serve_frontend(tmp_path, monkeypatch) as client:
        index = await client.get("/")
        for path in ("/health", "/api/health", "/jobs", "/api/jobs"):
            assert (await client.get(path)).status_code == 401, path
            assert (await client.get(path, headers=key)).status_code == 200, path

    assert index.status_code == 200
    assert "reelsmith-spa" in index.text


def test_frontend_dist_defaults_to_web_dist():
    assert Path(app_main.FRONTEND_DIST) == (
        Path(app_main.__file__).parents[1] / "web" / "dist"
    )
