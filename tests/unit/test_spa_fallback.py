"""``SpaFallbackMiddleware`` serves ``index.html`` for client routes (T036).

The middleware wraps a recording ASGI app here, so the tests see exactly the
scope the router would see, including ``raw_path``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.spa_fallback import (
    API_ONLY_PATHS,
    CLIENT_ROUTES,
    SpaFallbackMiddleware,
    compile_route,
    is_client_route,
    prefers_html,
)

HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


class _Recorder:
    """ASGI app that records the scope it is called with and sends two body
    chunks, waiting for ``release`` in between."""

    def __init__(self) -> None:
        self.scopes: list[dict[str, Any]] = []
        self.release = asyncio.Event()

    async def __call__(self, scope, receive, send) -> None:
        self.scopes.append(scope)
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"one", "more_body": True})
        await self.release.wait()
        await send({"type": "http.response.body", "body": b"two", "more_body": False})


@pytest.fixture
def dist(tmp_path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "share").mkdir()
    (root / "index.html").write_text("<!doctype html>")
    (root / "assets" / "app.js").write_text("js")
    (root / "share" / "card.png").write_bytes(b"png")
    return root


def _scope(
    path: str,
    *,
    accept: str | None = HTML,
    method: str = "GET",
    raw_path: bytes | None = None,
    **extra: Any,
) -> dict[str, Any]:
    headers = [(b"host", b"test")]
    if accept is not None:
        headers.append((b"accept", accept.encode("latin-1")))
    scope: dict[str, Any] = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": raw_path if raw_path is not None else path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": headers,
    }
    scope.update(extra)
    return scope


async def _seen(scope: dict[str, Any], dist: Path) -> dict[str, Any]:
    inner = _Recorder()
    inner.release.set()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        pass

    await SpaFallbackMiddleware(inner, dist=dist)(scope, receive, send)
    [seen] = inner.scopes
    return seen


# ── Accept negotiation ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "accept",
    [
        # Chrome, Firefox and Safari top-level navigations
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "text/html",
        "TEXT/HTML",
        " text/html ; level=1 ",
        "application/xml;q=0.9, text/html;q=0.95",
        "text/html;q=0.5, application/json;q=0.5",
    ],
)
def test_prefers_html_for_navigations(accept):
    assert prefers_html(accept) is True


@pytest.mark.parametrize(
    "accept",
    [
        "",
        "*/*",  # fetch() default
        "text/*",
        "application/json",
        "text/event-stream",  # EventSource
        "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",  # <img>
        "application/json, text/html;q=0.5",
        "text/html;q=0",
        "text/html;q=0.0",
        "text/html;q=abc",
        "text/htmlx",
        "application/xhtml+xml",
    ],
)
def test_does_not_prefer_html_otherwise(accept):
    assert prefers_html(accept) is False


# ── The client-route table ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/workflow",
        "/jobs/new",
        "/jobs/0123abcd",
        "/generate/new",
        "/uploads/new",
        "/clips/3f2b-11",
        "/clips/3f2b-11/edit",
        "/clips/3f2b-11/publish",
        "/settings/brand",
        "/settings/social",
        "/settings/captions",
        "/settings/api",
        "/settings/webhooks",
        "/team",
        "/calendar",
        "/analytics",
        "/share/tok",
        "/share/rs.eyJjIjoiMSJ9.c2ln",
    ],
)
def test_client_routes_match(path):
    assert is_client_route(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/jobs",
        "/clips",
        "/settings",
        "/share",
        "/health",
        "/docs",
        "/openapi.json",
        "/index.html",
        "/assets/app.js",
        "/favicon.svg",
        "/clips/bulk-export.zip",  # API_ONLY_PATHS: not a clip id
        "/jobs/preview",  # API_ONLY_PATHS: not a job id
        "/clips/c1/export.xml",
        "/clips/c1/video",
        "/clips/c1/thumbnail",
        "/clips/c1/edit/plan",
        "/jobs/j1/events",
        "/jobs/preview/thumbnail",
        "/uploads/new/",
        "/workflow/",
        "/clips//edit",
        "/clips/../edit",
        "/clips/./edit",
        "/share/..",
        "/api/jobs/abc",
        "/api/uploads/new",
        "/Workflow",
        "/workflowx",
    ],
)
def test_other_paths_do_not_match(path):
    assert is_client_route(path) is False


def test_api_only_paths_would_otherwise_be_captured_by_a_parameter():
    """Each entry earns its place: without the exclusion a ``$param`` route
    would match it."""
    patterns = [compile_route(shape) for shape in CLIENT_ROUTES]
    for path in API_ONLY_PATHS:
        assert any(p.fullmatch(path) for p in patterns), path


@pytest.mark.parametrize(
    ("shape", "match", "no_match"),
    [
        ("/", "/", "/x"),
        ("/team", "/team", "/team/x"),
        ("/jobs/$jobId", "/jobs/a-b_c.d", "/jobs/a/b"),
        ("/clips/$clipId/edit", "/clips/x/edit", "/clips/x/y/edit"),
        ("/a.b/$x", "/a.b/1", "/aXb/1"),  # literals are escaped
    ],
)
def test_compile_route(shape, match, no_match):
    pattern = compile_route(shape)

    assert pattern.fullmatch(match)
    assert not pattern.fullmatch(no_match)


@pytest.mark.parametrize(
    "shape", ["jobs", "/files/$", "/posts/{-$id}", "/f/{$id}.txt", "/a//b", "/$"]
)
def test_compile_route_rejects_unsupported_syntax(shape):
    with pytest.raises(ValueError):
        compile_route(shape)


# ── The middleware ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("path", ["/uploads/new", "/jobs/abc", "/clips/x/edit", "/"])
async def test_rewrites_html_navigations_to_client_routes(dist, path, method):
    scope = _scope(path, method=method, query_string=b"a=1")

    seen = await _seen(scope, dist)

    assert seen["path"] == "/index.html"
    assert seen["raw_path"] == b"/index.html"
    # Everything else is kept.
    assert seen["query_string"] == b"a=1"
    assert seen["method"] == method
    assert seen["headers"] == scope["headers"]


async def test_reads_accept_split_over_two_headers(dist):
    scope = _scope("/uploads/new", accept=None)
    scope["headers"] += [(b"accept", b"text/html"), (b"accept", b"*/*;q=0.8")]

    assert (await _seen(scope, dist))["path"] == "/index.html"


@pytest.mark.parametrize(
    "scope_kwargs",
    [
        {"accept": "application/json"},
        {"accept": "*/*"},
        {"accept": "text/event-stream"},
        {"accept": None},
        {"method": "POST"},
        {"method": "PUT"},
        {"method": "DELETE"},
        {"method": "OPTIONS"},
    ],
    ids=lambda kw: ",".join(f"{k}={v}" for k, v in kw.items()),
)
async def test_leaves_api_requests_alone(dist, scope_kwargs):
    scope = _scope("/jobs/abc", **scope_kwargs)

    assert await _seen(scope, dist) is scope


@pytest.mark.parametrize(
    "path",
    [
        "/api/jobs/abc",  # /api is always the API
        "/api/uploads/new",
        "/api",
        "/health",
        "/clips/bulk-export.zip",
        "/clips/c1/export.xml",
        "/assets/app.js",
        "/share/card.png",  # a real file in dist with a client-route shape
    ],
)
async def test_leaves_other_paths_alone(dist, path):
    scope = _scope(path)

    assert await _seen(scope, dist) is scope


async def test_does_nothing_without_index_html(dist):
    (dist / "index.html").unlink()
    scope = _scope("/uploads/new")

    assert await _seen(scope, dist) is scope


async def test_does_nothing_when_index_html_is_not_a_file(dist):
    (dist / "index.html").unlink()
    (dist / "index.html").mkdir()
    scope = _scope("/uploads/new")

    assert await _seen(scope, dist) is scope


async def test_keeps_root_path(dist):
    scope = _scope(
        "/reelsmith/jobs/abc", raw_path=b"/reelsmith/jobs/abc", root_path="/reelsmith"
    )

    seen = await _seen(scope, dist)

    assert seen["path"] == "/reelsmith/index.html"
    assert seen["raw_path"] == b"/reelsmith/index.html"
    assert seen["root_path"] == "/reelsmith"


async def test_api_under_root_path_is_left_alone(dist):
    scope = _scope("/reelsmith/api/jobs/abc", root_path="/reelsmith")

    assert await _seen(scope, dist) is scope


async def test_scope_without_raw_path_gets_none_added(dist):
    scope = _scope("/uploads/new")
    del scope["raw_path"]

    seen = await _seen(scope, dist)

    assert seen["path"] == "/index.html"
    assert "raw_path" not in seen


@pytest.mark.parametrize("kind", ["websocket", "lifespan"])
async def test_non_http_scopes_pass_through(dist, kind):
    scope = {
        "type": kind,
        "path": "/uploads/new",
        "headers": [(b"accept", b"text/html")],
    }

    assert await _seen(scope, dist) is scope


async def test_receive_and_send_pass_through_and_bodies_stream(dist):
    """Pure ASGI: the first body chunk reaches the client before the inner app
    finishes (a buffering middleware would hold it)."""
    inner = _Recorder()
    sent: list[dict[str, Any]] = []
    first_chunk = asyncio.Event()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message.get("body") == b"one":
            first_chunk.set()

    task = asyncio.create_task(
        SpaFallbackMiddleware(inner, dist=dist)(_scope("/uploads/new"), receive, send)
    )
    await asyncio.wait_for(first_chunk.wait(), timeout=2)
    assert not task.done()
    inner.release.set()
    await asyncio.wait_for(task, timeout=2)

    assert [m.get("body") for m in sent[1:]] == [b"one", b"two"]
    assert inner.scopes[0]["path"] == "/index.html"


# ── Vary: Accept ─────────────────────────────────────────────────────────────


async def _response_headers(
    scope: dict[str, Any], dist: Path, inner_headers: list[tuple[bytes, bytes]]
) -> list[tuple[bytes, bytes]]:
    """Headers of the ``http.response.start`` the client receives when the
    inner app answers with ``inner_headers``."""

    async def inner(scope, receive, send) -> None:
        await send(
            {"type": "http.response.start", "status": 200, "headers": inner_headers}
        )
        await send({"type": "http.response.body", "body": b"x", "more_body": False})

    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await SpaFallbackMiddleware(inner, dist=dist)(scope, receive, send)
    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
    assert sent[1]["body"] == b"x"
    return list(sent[0]["headers"])


def _vary(headers: list[tuple[bytes, bytes]]) -> list[bytes]:
    return [value for name, value in headers if name.lower() == b"vary"]


@pytest.mark.parametrize("accept", [HTML, "application/json", "*/*", None])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_both_answers_on_a_client_route_vary_on_accept(dist, accept, method):
    scope = _scope("/jobs/abc", accept=accept, method=method)

    headers = await _response_headers(scope, dist, [(b"content-type", b"x/y")])

    assert _vary(headers) == [b"Accept"]
    assert (b"content-type", b"x/y") in headers


@pytest.mark.parametrize(
    ("existing", "want"),
    [
        (b"Origin", b"Origin, Accept"),
        (b"Accept-Encoding", b"Accept-Encoding, Accept"),
        (b"origin, accept", b"origin, accept"),
        (b"*", b"*"),
    ],
)
async def test_vary_merges_with_an_existing_vary(dist, existing, want):
    headers = await _response_headers(
        _scope("/uploads/new"), dist, [(b"Vary", existing)]
    )

    assert _vary(headers) == [want]


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/jobs/abc", "GET"),  # /api never negotiates
        ("/health", "GET"),
        ("/clips/bulk-export.zip", "GET"),
        ("/assets/app.js", "GET"),
        ("/share/card.png", "GET"),  # a real file
        ("/jobs/abc", "POST"),
        ("/jobs/abc", "DELETE"),
    ],
)
async def test_no_vary_where_accept_changes_nothing(dist, path, method):
    headers = await _response_headers(_scope(path, method=method), dist, [])

    assert _vary(headers) == []


async def test_no_vary_without_index_html(dist):
    (dist / "index.html").unlink()

    headers = await _response_headers(_scope("/jobs/abc"), dist, [])

    assert _vary(headers) == []
