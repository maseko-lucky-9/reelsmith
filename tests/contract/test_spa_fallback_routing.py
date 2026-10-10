"""Reloading a client route under ``serve_frontend`` gets the UI (T036, FR-062).

The built UI is served at ``/`` by ``StaticFiles``, which has no history
fallback, and some client routes are also API paths (``/jobs/{id}``,
``/clips/{id}/edit``). ``SpaFallbackMiddleware`` (``app/spa_fallback.py``)
serves ``index.html`` for a GET or HEAD that prefers HTML and names a client
route; every other request reaches the API exactly as before. Paths under
``/api`` are always the API.

Every test that can reach a route using ``get_session`` overrides it with an
in-memory SQLite engine, so nothing touches the developer's database.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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

INDEX_HTML = "<!doctype html><title>reelsmith-spa</title>"
ASSET_JS = "console.log('spa')"
# A real file at a path shaped like a client route (/share/$token).
SHARE_FILE = b"\x89PNG not-really"
GENERIC_404 = {"detail": "Not Found"}

# What browsers send for a top-level navigation (Chrome, Firefox).
HTML = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
    "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
)
FIREFOX_HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
JSON = "application/json"


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


def _write_dist(tmp_path, *, index: bool = True):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "share").mkdir()
    if index:
        (dist / "index.html").write_text(INDEX_HTML)
    (dist / "assets" / "app.js").write_text(ASSET_JS)
    (dist / "favicon.svg").write_text("<svg/>")
    (dist / "share" / "card.png").write_bytes(SHARE_FILE)
    return dist


@asynccontextmanager
async def _client(
    tmp_path, monkeypatch, *, serve_frontend: bool = True, index: bool = True
) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """App built with a temporary ``web/dist``; yields the client and the id
    of the one clip in the in-memory database."""
    monkeypatch.setattr(settings, "serve_frontend", serve_frontend)
    monkeypatch.setattr(app_main, "FRONTEND_DIST", _write_dist(tmp_path, index=index))

    app = create_app()
    async with _memory_db(app) as clip_id, LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, clip_id


@pytest.fixture
async def served(tmp_path, monkeypatch) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    async with _client(tmp_path, monkeypatch) as pair:
        yield pair


def _is_spa(response: httpx.Response) -> bool:
    return (
        response.status_code == 200
        and response.headers["content-type"].startswith("text/html")
        and response.text == INDEX_HTML
    )


# One concrete path per client route in web/src/routeTree.ts, written out by
# hand so this test does not depend on the table it checks.
CLIENT_PATHS = [
    "/",
    "/workflow",
    "/workflow?url=https%3A%2F%2Fyoutu.be%2Fabc",
    "/jobs/new",
    "/jobs/abc",
    "/generate/new",
    "/uploads/new",
    "/clips/xyz",
    "/clips/xyz/edit",
    "/clips/xyz/publish",
    "/settings/brand",
    "/settings/social",
    "/settings/captions",
    "/settings/api",
    "/settings/webhooks",
    "/team",
    "/analytics",
    "/share/tok",
    "/share/rs.eyJjIjoiMSJ9.c2ln",  # share tokens are rs.<payload>.<sig>
]


@pytest.mark.parametrize("accept", [HTML, FIREFOX_HTML])
@pytest.mark.parametrize("path", CLIENT_PATHS)
async def test_html_navigation_to_a_client_route_gets_the_spa(served, path, accept):
    client, _ = served

    response = await client.get(path, headers={"Accept": accept})

    assert _is_spa(response), (response.status_code, response.text[:200])


@pytest.mark.parametrize(
    "accept",
    [
        JSON,
        "*/*",  # fetch() default: what the UI's apiFetch sends
        "text/event-stream",  # EventSource
        "application/json, text/html;q=0.5",
        "text/html;q=0",
    ],
)
async def test_api_requests_to_shared_paths_still_get_the_api(served, accept):
    """``/jobs/{id}``, ``/jobs/new``, ``/clips/{id}`` and ``/clips/{id}/edit``
    are both client routes and API routes: only an HTML navigation gets the
    SPA."""
    client, clip_id = served
    headers = {"Accept": accept}

    job = await client.get("/jobs/abc", headers=headers)
    new = await client.get("/jobs/new", headers=headers)
    edit = await client.get(f"/clips/{clip_id}/edit", headers=headers)
    clip = await client.get("/clips/xyz", headers=headers)

    assert (job.status_code, job.json()) == (404, {"detail": "job not found: abc"})
    assert (new.status_code, new.json()) == (404, {"detail": "job not found: new"})
    assert (clip.status_code, clip.json()) == (404, {"detail": "clip not found"})
    assert (edit.status_code, edit.json()) == (
        404,
        {"detail": "no edit state for clip"},
    )


async def test_requests_without_an_accept_header_get_the_api(served):
    client, _ = served
    request = client.build_request("GET", "/jobs/abc")
    del request.headers["Accept"]

    response = await client.send(request)

    assert (response.status_code, response.json()) == (
        404,
        {"detail": "job not found: abc"},
    )


# API and doc paths a browser may navigate to (downloads, media, docs). None is
# a client route, so the Accept header must not change the response.
NOT_CLIENT_PATHS = [
    "/clips/{clip_id}/export.xml?format=premiere",
    "/clips/bulk-export.zip?ids=not-real",
    "/clips/bulk-export.zip",
    "/clips/{clip_id}/video",
    "/clips/{clip_id}/thumbnail",
    "/clips/{clip_id}/edit/plan",
    "/media/clip.mp4",
    "/health",
    "/jobs",
    "/jobs/preview",  # 422 (no url): never spawns yt-dlp
    "/jobs/abc/events",
    "/clips",
    "/brand-templates",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/uploads/new/",
    "/settings",
    "/no-such-page",
    "/calendar",  # page removed with scheduled publishing (FR-032, T045)
]


@pytest.mark.parametrize("path", NOT_CLIENT_PATHS)
async def test_html_navigation_to_other_paths_is_not_rewritten(served, path):
    client, clip_id = served
    path = path.format(clip_id=clip_id)

    as_html = await client.get(path, headers={"Accept": HTML})
    as_json = await client.get(path, headers={"Accept": JSON})

    assert (as_html.status_code, as_html.content) == (
        as_json.status_code,
        as_json.content,
    )
    assert INDEX_HTML not in as_html.text


async def test_html_navigation_to_the_removed_calendar_page_gets_the_api_404(served):
    """``/calendar`` went with scheduled publishing (FR-032, T045): a reload
    gets the API's plain 404, not the shell and not a server error."""
    client, _ = served

    response = await client.get("/calendar", headers={"Accept": HTML})

    assert (response.status_code, response.json()) == (404, GENERIC_404)


@pytest.mark.parametrize(
    ("path", "want"),
    [
        ("/api/jobs/abc", (404, {"detail": "job not found: abc"})),
        ("/api/jobs/new", (404, {"detail": "job not found: new"})),
        ("/api/uploads/new", (404, GENERIC_404)),
        ("/api/settings/brand", (404, GENERIC_404)),
    ],
)
async def test_html_navigation_under_api_gets_the_api(served, path, want):
    """Decision: ``/api/...`` is the API's address space. The React router has
    no ``/api`` base path, so the SPA there would only render its not-found
    page; every browser-navigated link the UI builds (XML export, bulk zip,
    clip video) lives under ``/api``."""
    client, _ = served

    response = await client.get(path, headers={"Accept": HTML})

    assert (response.status_code, response.json()) == want


async def test_ui_download_links_under_api_are_not_rewritten(served):
    """The links ``web/src`` builds for browser navigation, as a browser
    navigates them."""
    client, clip_id = served
    for path in (
        f"/api/clips/{clip_id}/export.xml?format=davinci",
        "/api/clips/bulk-export.zip?ids=not-real",
        f"/api/clips/{clip_id}/video",
        f"/api/clips/{clip_id}/thumbnail",
        f"/api/clips/{clip_id}/edit",
        "/api/jobs/preview/thumbnail",  # 422 (no url): never spawns yt-dlp
    ):
        as_html = await client.get(path, headers={"Accept": HTML})
        as_json = await client.get(path, headers={"Accept": JSON})
        assert (as_html.status_code, as_html.content) == (
            as_json.status_code,
            as_json.content,
        ), path
        assert INDEX_HTML not in as_html.text, path


async def test_shared_paths_vary_on_accept(served):
    """One URL, two representations: both carry ``Vary: Accept`` so a cache
    keys on it. CORS's ``Vary: Origin`` is kept."""
    client, _ = served
    origin = {"Origin": "http://localhost:5173"}

    page = await client.get("/jobs/abc", headers={"Accept": HTML, **origin})
    data = await client.get("/jobs/abc", headers={"Accept": JSON, **origin})
    other = await client.get("/health", headers={"Accept": HTML, **origin})
    prefixed = await client.get("/api/jobs/abc", headers={"Accept": HTML, **origin})

    assert _is_spa(page)
    assert data.status_code == 404
    for response in (page, data):
        vary = {v.strip().lower() for v in response.headers["vary"].split(",")}
        assert {"accept", "origin"} <= vary, response.headers["vary"]
    for response in (other, prefixed):
        assert "accept" not in response.headers.get("vary", "").lower()


async def test_real_static_files_win_over_the_fallback(served):
    client, _ = served

    asset = await client.get("/assets/app.js", headers={"Accept": HTML})
    icon = await client.get("/favicon.svg", headers={"Accept": HTML})
    # /share/card.png has the /share/$token shape but is a file in dist.
    card = await client.get("/share/card.png", headers={"Accept": HTML})

    assert (asset.status_code, asset.text) == (200, ASSET_JS)
    assert (icon.status_code, icon.text) == (200, "<svg/>")
    assert (card.status_code, card.content) == (200, SHARE_FILE)


@pytest.mark.parametrize(
    ("method", "path", "kwargs", "want_status"),
    [
        ("DELETE", "/clips/missing/edit", {}, 404),
        ("PUT", "/clips/missing/edit", {"json": {}}, 422),
        ("POST", "/jobs/new", {}, 405),
        ("POST", "/uploads/new", {}, 405),
    ],
)
async def test_non_get_methods_on_client_routes_are_not_rewritten(
    served, method, path, kwargs, want_status
):
    client, _ = served

    as_html = await client.request(method, path, headers={"Accept": HTML}, **kwargs)
    as_json = await client.request(method, path, headers={"Accept": JSON}, **kwargs)

    assert as_html.status_code == want_status
    assert (as_html.status_code, as_html.content) == (
        as_json.status_code,
        as_json.content,
    )


@pytest.mark.parametrize("path", ["/uploads/new", "/jobs/abc", "/clips/xyz/edit"])
async def test_head_on_a_client_route_gets_the_spa_headers(served, path):
    client, _ = served

    response = await client.head(path, headers={"Accept": HTML})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-length"] == str(len(INDEX_HTML))
    assert response.content == b""


async def test_serve_frontend_off_changes_nothing(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch, serve_frontend=False) as (client, _):
        upload = await client.get("/uploads/new", headers={"Accept": HTML})
        job = await client.get("/jobs/abc", headers={"Accept": HTML})
        root = await client.get("/", headers={"Accept": HTML})

    assert (upload.status_code, upload.json()) == (404, GENERIC_404)
    assert (job.status_code, job.json()) == (404, {"detail": "job not found: abc"})
    assert (root.status_code, root.json()) == (404, GENERIC_404)


async def test_dist_without_index_html_changes_nothing(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch, index=False) as (client, _):
        upload = await client.get("/uploads/new", headers={"Accept": HTML})
        job = await client.get("/jobs/abc", headers={"Accept": HTML})
        asset = await client.get("/assets/app.js")

    assert upload.status_code == 404
    assert (job.status_code, job.json()) == (404, {"detail": "job not found: abc"})
    assert (asset.status_code, asset.text) == (200, ASSET_JS)


async def test_with_auth_the_spa_shell_stays_open_and_the_api_closed(
    tmp_path, monkeypatch
):
    """FR-060: the static mount is not an API route, so the API-key dependency
    does not cover the shell, as for ``/`` (T034); every API request on a
    shared path still needs the key."""
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "api_key", "test-key")
    key = {"Authorization": "Bearer test-key"}

    async with _client(tmp_path, monkeypatch) as (client, _):
        shells = [
            await client.get(path, headers={"Accept": HTML})
            for path in ("/", "/uploads/new", "/settings/brand", "/jobs/abc")
        ]
        closed = [
            await client.get("/jobs/abc", headers={"Accept": JSON}),
            await client.get("/jobs/abc"),
            await client.get("/api/jobs/abc", headers={"Accept": HTML}),
            await client.get("/health", headers={"Accept": HTML}),
        ]
        with_key = await client.get("/api/jobs/abc", headers={"Accept": JSON, **key})

    assert all(_is_spa(r) for r in shells), [r.status_code for r in shells]
    assert [r.status_code for r in closed] == [401, 401, 401, 401]
    assert (with_key.status_code, with_key.json()) == (
        404,
        {"detail": "job not found: abc"},
    )


DOCS_PATHS = [
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/openapi.json",
    "/api/docs",
    "/api/docs/oauth2-redirect",
    "/api/redoc",
    "/api/openapi.json",
]


@pytest.mark.parametrize("accept", [HTML, JSON])
async def test_with_auth_the_switched_off_docs_get_the_404_not_the_shell(
    tmp_path, monkeypatch, accept
):
    """FR-060, T043: auth on switches the docs routes off. No docs path is a
    client route and none is a file in dist, so the SPA fallback does not
    answer for it and the static mount's own 404 is the response: a browser
    navigation gets the same plain 404 as ``fetch``, with or without the key."""
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "api_key", "test-key")
    key = {"Authorization": "Bearer test-key"}

    async with _client(tmp_path, monkeypatch) as (client, _):
        responses = [
            await client.get(path, headers={"Accept": accept, **extra})
            for path in DOCS_PATHS
            for extra in ({}, key)
        ]

    assert [r.status_code for r in responses] == [404] * (2 * len(DOCS_PATHS))
    assert all(r.json() == GENERIC_404 for r in responses)


# ── SSE under serve_frontend ─────────────────────────────────────────────────


@pytest.mark.parametrize("accept", [b"text/event-stream", HTML.encode()])
async def test_job_events_stream_under_serve_frontend(tmp_path, monkeypatch, accept):
    """Drive the ASGI app directly: the first SSE event must reach the client
    while the stream is still open. ``/jobs/{id}/events`` is not a client
    route, so not even an HTML Accept header rewrites it."""
    monkeypatch.setattr(settings, "serve_frontend", True)
    monkeypatch.setattr(app_main, "FRONTEND_DIST", _write_dist(tmp_path))
    app = create_app()
    async with LifespanManager(app):
        await app.state.job_store.create(
            JobState(
                job_id="job-sse", url="https://example.com/v", download_path="/tmp"
            )
        )
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

        path = "/jobs/job-sse/events"
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
            "headers": [(b"host", b"test"), (b"accept", accept)],
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
        }

        await asyncio.wait_for(app(scope, receive, send), timeout=5)

    start = sent[0]
    assert start["status"] == 200
    assert dict(start["headers"])[b"content-type"].startswith(b"text/event-stream")
    assert any(
        m["type"] == "http.response.body"
        and b"event: FolderCreated" in m.get("body", b"")
        and m.get("more_body") is True
        for m in sent
    )
