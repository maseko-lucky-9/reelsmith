"""FR-060: ``YTVIDEO_REQUIRE_AUTH=true`` puts ``require_api_key`` on every route.

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


@pytest.mark.parametrize(
    "route",
    ["/docs", "/redoc", "/openapi.json", "/api/docs", "/api/redoc", "/api/openapi.json"],
)
def test_docs_routes_bypass_app_level_auth(secured, route):
    """Pins a known gap: FastAPI adds the docs routes outside the app-level
    ``dependencies``, so they stay open with auth on. Change this test on purpose
    if the gap is closed."""
    assert secured.get(route).status_code == 200


@pytest.mark.parametrize(
    "route", ["/clips/missing/ai-hook", "/api/clips/missing/ai-hook"]
)
def test_formerly_api_prefixed_route_needs_the_key_at_both_addresses(secured, route):
    """Rejected before any route dependency runs, so no database is opened."""
    assert secured.post(route).status_code == 401
    assert secured.post(route, params={"token": "wrong-key"}).status_code == 401
