"""FR-060: ``YTVIDEO_REQUIRE_AUTH=true`` puts ``require_api_key`` on every route
and switches FastAPI's docs surface (``/docs``, ``/redoc``, ``/openapi.json``)
off, since those routes would not get the key check (T043).

``create_app`` reads ``settings.require_auth`` when it builds the app, so the
settings are patched before ``create_app()`` runs. ``require_api_key`` accepts
the key as ``Authorization: Bearer <key>`` or as a ``?token=<key>`` query
parameter, and nothing else.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import settings

KEY = "test-key"
# Each route at both of its addresses: the API is served at /x and /api/x
# (T034), and the API key applies identically to both.
ROUTES = ["/health", "/clips", "/jobs", "/api/health", "/api/clips", "/api/jobs"]
# FastAPI's docs surface (Swagger UI and its OAuth2 redirect page, ReDoc, the
# schema), at both addresses. Served with auth off, switched off with auth on.
DOCS_ROUTES = [
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/openapi.json",
    "/api/docs",
    "/api/docs/oauth2-redirect",
    "/api/redoc",
    "/api/openapi.json",
]
NOT_FOUND = {"detail": "Not Found"}


def _client(monkeypatch: pytest.MonkeyPatch, *, require_auth: bool) -> TestClient:
    monkeypatch.setattr(settings, "require_auth", require_auth)
    monkeypatch.setattr(settings, "api_key", KEY)
    return TestClient(create_app())


@pytest.fixture
def secured(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with _client(monkeypatch, require_auth=True) as client:
        yield client


@pytest.fixture
def open_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with _client(monkeypatch, require_auth=False) as client:
        yield client


@pytest.mark.parametrize("route", ROUTES)
def test_no_key_is_401(secured, route):
    response = secured.get(route)

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}


@pytest.mark.parametrize("route", ROUTES)
def test_wrong_bearer_key_is_401(secured, route):
    response = secured.get(route, headers={"Authorization": "Bearer wrong-key"})

    assert response.status_code == 401


@pytest.mark.parametrize("route", ROUTES)
def test_wrong_token_query_is_401(secured, route):
    response = secured.get(route, params={"token": "wrong-key"})

    assert response.status_code == 401


@pytest.mark.parametrize("route", ROUTES)
def test_bearer_key_is_200(secured, route):
    response = secured.get(route, headers={"Authorization": f"Bearer {KEY}"})

    assert response.status_code == 200


@pytest.mark.parametrize("route", ROUTES)
def test_token_query_is_200(secured, route):
    response = secured.get(route, params={"token": KEY})

    assert response.status_code == 200


@pytest.mark.parametrize("route", ROUTES)
def test_auth_off_leaves_routes_open(open_app, route):
    assert open_app.get(route).status_code == 200


@pytest.mark.parametrize("route", DOCS_ROUTES)
def test_auth_on_disables_the_docs_routes(secured, route):
    """T043: FastAPI adds its docs routes outside the app-level ``dependencies``,
    so with auth on ``create_app`` switches them off instead of leaving them
    open. They answer the plain 404 of any unknown path, with or without the
    key: the Swagger page could not send the key on its own ``/openapi.json``
    fetch anyway."""
    without_key = secured.get(route)
    with_key = secured.get(route, headers={"Authorization": f"Bearer {KEY}"})

    assert [without_key.status_code, with_key.status_code] == [404, 404]
    assert without_key.json() == with_key.json() == NOT_FOUND


@pytest.mark.parametrize("route", DOCS_ROUTES)
def test_auth_off_keeps_the_docs_routes(open_app, route):
    assert open_app.get(route).status_code == 200


def test_auth_on_keeps_the_schema_in_process(monkeypatch):
    """Only the HTTP docs surface goes: ``app.openapi()``, which the route
    drift test and the T034 prefix test walk, still builds the schema."""
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "api_key", KEY)

    paths = create_app().openapi()["paths"]

    assert {"/health", "/clips", "/jobs"} <= set(paths)


@pytest.mark.parametrize(
    "route", ["/clips/missing/ai-hook", "/api/clips/missing/ai-hook"]
)
def test_formerly_api_prefixed_route_needs_the_key_at_both_addresses(secured, route):
    """Rejected before any route dependency runs, so no database is opened."""
    assert secured.post(route).status_code == 401
    assert secured.post(route, params={"token": "wrong-key"}).status_code == 401
