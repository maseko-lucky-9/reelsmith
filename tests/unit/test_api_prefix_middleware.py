"""``ApiPrefixMiddleware`` strips one leading ``/api`` segment (T034).

The middleware wraps a recording ASGI app here, so the tests see exactly the
scope the router would see, including ``raw_path``, which Starlette itself
never reads.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.api_prefix import ApiPrefixMiddleware


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


def _scope(path: str, raw_path: bytes | None = None, **extra: Any) -> dict[str, Any]:
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": path,
        "root_path": "",
        "query_string": b"",
        "headers": [],
    }
    if raw_path is not None:
        scope["raw_path"] = raw_path
    scope.update(extra)
    return scope


async def _seen(scope: dict[str, Any]) -> dict[str, Any]:
    inner = _Recorder()
    inner.release.set()
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await ApiPrefixMiddleware(inner)(scope, receive, send)
    [seen] = inner.scopes
    return seen


@pytest.mark.parametrize(
    ("path", "raw_path", "want_path", "want_raw"),
    [
        ("/api/jobs", b"/api/jobs", "/jobs", b"/jobs"),
        (
            "/api/clips/c1/ai-hook",
            b"/api/clips/c1/ai-hook",
            "/clips/c1/ai-hook",
            b"/clips/c1/ai-hook",
        ),
        ("/api/health", b"/api/health", "/health", b"/health"),
        ("/api", b"/api", "/", b"/"),
        ("/api/", b"/api/", "/", b"/"),
        ("/api/api/jobs", b"/api/api/jobs", "/api/jobs", b"/api/jobs"),
        ("/api/clips/a b", b"/api/clips/a%20b", "/clips/a b", b"/clips/a%20b"),
    ],
)
async def test_leading_api_segment_is_stripped_from_path_and_raw_path(
    path, raw_path, want_path, want_raw
):
    seen = await _seen(_scope(path, raw_path))

    assert seen["path"] == want_path
    assert seen["raw_path"] == want_raw


@pytest.mark.parametrize(
    "path",
    [
        "/apixyz",
        "/apixyz/jobs",
        "/api-docs",
        "/apijobs",
        "/jobs",
        "/health",
        "/",
        "/x/api/jobs",
    ],
)
async def test_other_paths_are_untouched(path):
    seen = await _seen(_scope(path, path.encode()))

    assert seen["path"] == path
    assert seen["raw_path"] == path.encode()


async def test_root_path_is_kept_and_the_api_segment_after_it_is_stripped():
    seen = await _seen(
        _scope("/proxy/api/jobs", b"/proxy/api/jobs", root_path="/proxy")
    )

    assert seen["root_path"] == "/proxy"
    assert seen["path"] == "/proxy/jobs"
    assert seen["raw_path"] == b"/proxy/jobs"


async def test_root_path_named_api_is_not_stripped_again():
    seen = await _seen(_scope("/api/jobs", b"/api/jobs", root_path="/api"))

    assert seen["path"] == "/api/jobs"
    assert seen["raw_path"] == b"/api/jobs"


async def test_scope_without_raw_path_gets_only_path_rewritten():
    seen = await _seen(_scope("/api/jobs"))

    assert seen["path"] == "/jobs"
    assert "raw_path" not in seen


async def test_encoded_prefix_that_raw_path_does_not_carry_is_left_alone():
    """``/%61pi/jobs`` decodes to ``/api/jobs``; rewriting only ``path`` would
    leave the two keys disagreeing, so the request is passed through as is."""
    seen = await _seen(_scope("/api/jobs", b"/%61pi/jobs"))

    assert seen["path"] == "/api/jobs"
    assert seen["raw_path"] == b"/%61pi/jobs"


async def test_websocket_scope_is_rewritten():
    seen = await _seen(_scope("/api/jobs/ws", b"/api/jobs/ws", type="websocket"))

    assert seen["path"] == "/jobs/ws"
    assert seen["raw_path"] == b"/jobs/ws"


async def test_lifespan_scope_passes_through_unchanged():
    scope = {"type": "lifespan", "asgi": {"version": "3.0"}}

    seen = await _seen(scope)

    assert seen is scope


async def test_callers_scope_is_not_mutated():
    scope = _scope("/api/jobs", b"/api/jobs")

    await _seen(scope)

    assert scope["path"] == "/api/jobs"
    assert scope["raw_path"] == b"/api/jobs"


async def test_body_chunks_are_forwarded_as_they_are_sent():
    """The first chunk must reach the server while the app is still running:
    the middleware may not buffer (SSE and the bulk zip stream)."""
    inner = _Recorder()
    sent: list[dict[str, Any]] = []
    first_chunk = asyncio.Event()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message.get("body") == b"one":
            first_chunk.set()

    call = asyncio.create_task(
        ApiPrefixMiddleware(inner)(_scope("/api/jobs", b"/api/jobs"), receive, send)
    )
    await asyncio.wait_for(first_chunk.wait(), timeout=2)
    assert not call.done()
    inner.release.set()
    await asyncio.wait_for(call, timeout=2)

    assert [m.get("body") for m in sent] == [None, b"one", b"two"]
