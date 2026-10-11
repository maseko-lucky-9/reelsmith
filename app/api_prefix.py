"""Serve the API at both ``/x`` and ``/api/x`` (T034).

The React client calls ``/api/...``. In dev the Vite proxy strips ``/api``
before forwarding; with ``YTVIDEO_SERVE_FRONTEND=true`` the browser talks to
FastAPI directly and nothing strips it. Routers are therefore mounted
unprefixed, and this middleware strips one leading ``/api`` segment before
routing, so both modes reach the same routes and the same app-level
dependencies (the API key).

Pure ASGI rather than ``BaseHTTPMiddleware``: it only replaces the scope and
passes ``receive`` and ``send`` through untouched, so streamed responses (SSE,
the bulk-export zip), client disconnects and websockets behave exactly as
without it.
"""

from __future__ import annotations

from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

API_PREFIX = "/api"


def strip_api_prefix(path: str, root_path: str = "") -> str | None:
    """Return ``path`` without the ``/api`` segment that follows ``root_path``,
    or ``None`` when it has none.

    ``/api`` alone becomes ``/``; ``/apixyz`` is not an ``/api`` segment.
    ``path`` includes ``root_path`` (ASGI), which is kept as is.
    """
    head = root_path if root_path and path.startswith(root_path + "/") else ""
    rest = path[len(head) :]
    if rest == API_PREFIX:
        return head + "/"
    if rest.startswith(API_PREFIX + "/"):
        return head + rest[len(API_PREFIX) :]
    return None


def _rewrite(scope: Scope) -> Scope:
    root_path = scope.get("root_path", "")
    path = strip_api_prefix(scope["path"], root_path)
    if path is None:
        return scope
    rewritten: dict[str, Any] = {**scope, "path": path}
    raw_path = scope.get("raw_path")
    if raw_path is not None:
        raw = strip_api_prefix(raw_path.decode("latin-1"), root_path)
        if raw is None:
            # The decoded path names /api but the raw bytes do not (for example
            # /%61pi/jobs): leave the request alone rather than route on one
            # value and log the other.
            return scope
        rewritten["raw_path"] = raw.encode("latin-1")
    return rewritten


class ApiPrefixMiddleware:
    """Strip a leading ``/api`` path segment from http and websocket scopes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            scope = _rewrite(scope)
        await self.app(scope, receive, send)
