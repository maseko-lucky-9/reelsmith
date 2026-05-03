"""Contract tests for /brand-templates router.

Uses an in-memory SQLite engine (aiosqlite) with dependency_overrides so
tests never touch the dev database and are fully isolated from each other.
"""
from __future__ import annotations

import pytest
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.db.base import Base
from app.db.session import get_session
from app.main import create_app


@pytest.fixture
async def brand_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        async with factory() as session:
            yield session

    app_inst = create_app()
    app_inst.dependency_overrides[get_session] = _override

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_inst), base_url="http://test"
    ) as client:
        yield client

    await engine.dispose()


async def test_list_templates_empty(brand_client):
    response = await brand_client.get("/brand-templates")
    assert response.status_code == 200
    assert response.json() == []


async def test_create_template_returns_201(brand_client):
    response = await brand_client.post(
        "/brand-templates",
        json={"name": "Test Brand", "primary_color": "#ff0000"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Test Brand"
    assert body["primary_color"] == "#ff0000"
    assert "id" in body


async def test_get_template_returns_created(brand_client):
    create = await brand_client.post(
        "/brand-templates", json={"name": "MyBrand"}
    )
    template_id = create.json()["id"]

    response = await brand_client.get(f"/brand-templates/{template_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "MyBrand"


async def test_get_unknown_template_returns_404(brand_client):
    response = await brand_client.get("/brand-templates/does-not-exist")
    assert response.status_code == 404


async def test_update_template_name(brand_client):
    create = await brand_client.post(
        "/brand-templates", json={"name": "Old Name"}
    )
    template_id = create.json()["id"]

    response = await brand_client.put(
        f"/brand-templates/{template_id}",
        json={"name": "New Name"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"


async def test_update_unknown_template_returns_404(brand_client):
    response = await brand_client.put(
        "/brand-templates/does-not-exist",
        json={"name": "Whatever"},
    )
    assert response.status_code == 404


async def test_delete_template(brand_client):
    create = await brand_client.post(
        "/brand-templates", json={"name": "ToDelete"}
    )
    template_id = create.json()["id"]

    delete = await brand_client.delete(f"/brand-templates/{template_id}")
    assert delete.status_code == 204

    get = await brand_client.get(f"/brand-templates/{template_id}")
    assert get.status_code == 404


async def test_delete_unknown_template_returns_404(brand_client):
    response = await brand_client.delete("/brand-templates/does-not-exist")
    assert response.status_code == 404


async def test_list_templates_shows_created(brand_client):
    await brand_client.post("/brand-templates", json={"name": "Alpha"})
    await brand_client.post("/brand-templates", json={"name": "Beta"})

    response = await brand_client.get("/brand-templates")
    assert response.status_code == 200
    names = {t["name"] for t in response.json()}
    assert {"Alpha", "Beta"} <= names


async def test_upload_asset_unknown_type_returns_400(brand_client):
    create = await brand_client.post(
        "/brand-templates", json={"name": "AssetTest"}
    )
    template_id = create.json()["id"]

    response = await brand_client.post(
        f"/brand-templates/{template_id}/assets",
        params={"asset_type": "unknown_type"},
        files={"file": ("test.png", b"\x89PNG", "image/png")},
    )
    assert response.status_code == 400


async def test_upload_asset_wrong_mime_returns_415(brand_client):
    create = await brand_client.post(
        "/brand-templates", json={"name": "AssetTest2"}
    )
    template_id = create.json()["id"]

    response = await brand_client.post(
        f"/brand-templates/{template_id}/assets",
        params={"asset_type": "logo"},
        files={"file": ("test.mp4", b"\x00", "video/mp4")},
    )
    assert response.status_code == 415
