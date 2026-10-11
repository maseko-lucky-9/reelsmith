"""Serve the UI's ``index.html`` when a browser loads a client route (T036).

With ``YTVIDEO_SERVE_FRONTEND=true`` the built React app is mounted at ``/``
by ``StaticFiles``, which has no history fallback: reloading ``/uploads/new``
got a 404, and reloading ``/jobs/<id>`` got the API's JSON, because the UI's
``/jobs/$jobId`` is also an API path.

``SpaFallbackMiddleware`` rewrites a request's path to ``/index.html``, so the
static mount serves the shell with its usual headers, when all of these hold:

1. the method is GET or HEAD;
2. the ``Accept`` header prefers ``text/html`` (``prefers_html``): browsers
   send that for a top-level navigation, while ``fetch`` sends ``*/*``,
   ``EventSource`` ``text/event-stream`` and ``<img>``/``<video>`` image or
   media types;
3. the path is not under ``/api``: that prefix is the API's address space
   (ADR-005), and every link the UI builds for browser navigation (XML
   export, bulk-export zip, clip video) lives there. The React router has no
   ``/api`` base path, so the shell there would only show its not-found page;
4. the path matches ``CLIENT_ROUTES``, the paths the React router renders
   (``web/src/routeTree.ts``), and is not in ``API_ONLY_PATHS``;
5. ``index.html`` exists in the dist directory and the path is not a real
   file there (assets, the favicon and other static files win).

Shared paths. ``/jobs/{id}``, ``/jobs/new``, ``/clips/{id}`` and
``/clips/{id}/edit`` are both client routes and API routes. An HTML-preferring GET or HEAD gets
the SPA; every other request (``fetch``, ``EventSource``, ``curl``, any non-GET) gets the API,
unchanged.
Because one URL then has two representations, every GET or HEAD that meets
conditions 1 and 3-5 gets ``Vary: Accept`` on its response (merged into an
existing ``Vary``), whichever one it got, so a cache never replays the shell
to ``fetch`` or the JSON to a navigation.

``CLIENT_ROUTES`` is the single Python copy of the React route table, in
TanStack Router syntax; ``tests/unit/test_spa_client_routes_drift.py`` parses
``web/src`` and fails when the two drift apart, and checks that every fixed
API GET path a client route would capture is in ``API_ONLY_PATHS``.

Added in ``create_app`` only with ``serve_frontend`` on and after
``ApiPrefixMiddleware``, so it runs first and still sees the ``/api`` prefix.
Pure ASGI, like ``ApiPrefixMiddleware``: it replaces the scope, passes
``receive`` through and touches only the headers of ``http.response.start``,
so streamed responses are unaffected.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api_prefix import strip_api_prefix

# The paths the React router renders (web/src/routeTree.ts), TanStack syntax:
# ``$name`` is one non-empty path segment. Keep in step with the route tree;
# the drift test names any difference.
CLIENT_ROUTES: tuple[str, ...] = (
    "/",
    "/workflow",
    "/jobs/new",
    "/jobs/$jobId",
    "/generate/new",
    "/uploads/new",
    "/clips/$clipId",
    "/clips/$clipId/edit",
    "/clips/$clipId/publish",
    "/settings/brand",
    "/settings/social",
    "/settings/captions",
    "/settings/api",
    "/settings/webhooks",
    "/team",
    "/analytics",
    "/share/$token",
)

# Fixed API GET paths that a ``$param`` above would capture. They are API
# resources (a download, a JSON lookup), never a job or clip id, so a browser
# navigating to them gets the API.
API_ONLY_PATHS: frozenset[str] = frozenset(
    {
        "/clips/bulk-export.zip",  # /clips/$clipId
        "/jobs/preview",  # /jobs/$jobId
    }
)

INDEX_HTML = "index.html"

_PARAM = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")
_LITERAL = re.compile(r"[^$/{}]+")
# One path segment, but not ``.`` or ``..``.
_SEGMENT = r"(?!\.\.?(?:/|$))[^/]+"


def compile_route(shape: str) -> re.Pattern[str]:
    """Compile a TanStack Router path to a regex for the whole path.

    Supports literal segments and ``$name`` parameters (one non-empty
    segment). Splats (``$``), optional (``{-$x}``) and prefixed or suffixed
    (``{$x}.txt``) parameters raise ``ValueError``: the route tree uses none,
    and matching them would need more than one segment pattern.
    """
    if shape == "/":
        return re.compile("/")
    if not shape.startswith("/"):
        raise ValueError(f"route path must start with '/': {shape!r}")
    parts: list[str] = []
    for segment in shape[1:].split("/"):
        if _PARAM.fullmatch(segment):
            parts.append(_SEGMENT)
        elif _LITERAL.fullmatch(segment):
            parts.append(re.escape(segment))
        else:
            raise ValueError(f"unsupported route segment {segment!r} in {shape!r}")
    return re.compile("/" + "/".join(parts))


CLIENT_ROUTE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    compile_route(shape) for shape in CLIENT_ROUTES
)


def is_client_route(path: str) -> bool:
    """True when the React router renders ``path`` (no ``root_path``)."""
    if path in API_ONLY_PATHS:
        return False
    return any(pattern.fullmatch(path) for pattern in CLIENT_ROUTE_PATTERNS)


def _quality(params: str) -> float:
    for param in params.split(";"):
        name, _, value = param.partition("=")
        if name.strip().lower() == "q":
            try:
                return min(max(float(value.strip()), 0.0), 1.0)
            except ValueError:
                return 0.0
    return 1.0


def prefers_html(accept: str) -> bool:
    """True when ``accept`` lists ``text/html`` with a non-zero quality that
    no other listed media range beats.

    A browser navigation (``text/html,application/xhtml+xml,...,*/*;q=0.8``)
    qualifies; ``*/*`` (``fetch``), ``text/event-stream`` (``EventSource``),
    ``application/json`` and ``application/json, text/html;q=0.5`` do not.
    """
    html = 0.0
    other = 0.0
    for item in accept.split(","):
        media, _, params = item.partition(";")
        media = media.strip().lower()
        if not media:
            continue
        quality = _quality(params)
        if media == "text/html":
            html = max(html, quality)
        else:
            other = max(other, quality)
    return html > 0.0 and html >= other


def _accept(scope: Scope) -> str:
    return ",".join(
        value.decode("latin-1") for name, value in scope["headers"] if name == b"accept"
    )


def _with_path(scope: Scope, path: str) -> Scope:
    rewritten: dict[str, Any] = {**scope, "path": path}
    if scope.get("raw_path") is not None:
        rewritten["raw_path"] = path.encode("latin-1")
    return rewritten


def _with_vary_accept(headers: Any) -> list[Any]:
    """``headers`` with ``Accept`` added to ``Vary`` (merged into an existing
    ``Vary``, such as CORS's ``Origin``)."""
    out = list(headers)
    for i, (name, value) in enumerate(out):
        if name.lower() == b"vary":
            tokens = {token.strip().lower() for token in value.split(b",")}
            if not tokens & {b"accept", b"*"}:
                out[i] = (name, value + b", Accept")
            return out
    out.append((b"vary", b"Accept"))
    return out


def _vary_on_accept(send: Send) -> Send:
    async def send_with_vary(message: Message) -> None:
        if message["type"] == "http.response.start":
            message = {
                **message,
                "headers": _with_vary_accept(message.get("headers", [])),
            }
        await send(message)

    return send_with_vary


class SpaFallbackMiddleware:
    """Rewrite HTML navigations to client routes to ``/index.html``."""

    def __init__(self, app: ASGIApp, dist: Path | str) -> None:
        self.app = app
        self.dist = Path(dist)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            index = self._index_for(scope)
            if index is not None:
                # The same URL answers with the SPA or the API depending on
                # Accept, so caches must key on it.
                send = _vary_on_accept(send)
                if prefers_html(_accept(scope)):
                    scope = _with_path(scope, index)
        await self.app(scope, receive, send)

    def _index_for(self, scope: Scope) -> str | None:
        """The ``index.html`` path when this request picks between the SPA and
        the API by ``Accept`` (a GET or HEAD for a client route the dist can
        serve), else ``None``."""
        if scope["method"] not in ("GET", "HEAD"):
            return None
        root_path = scope.get("root_path", "")
        path = scope["path"]
        if strip_api_prefix(path, root_path) is not None:
            return None
        head = root_path if root_path and path.startswith(root_path + "/") else ""
        route_path = path[len(head) :]
        if not is_client_route(route_path) or not self._serves(route_path):
            return None
        return f"{head}/{INDEX_HTML}"

    def _serves(self, route_path: str) -> bool:
        """``index.html`` exists and ``route_path`` is not a file in dist."""
        if not (self.dist / INDEX_HTML).is_file():
            return False
        relative = route_path.lstrip("/")
        return not relative or not (self.dist / relative).is_file()
