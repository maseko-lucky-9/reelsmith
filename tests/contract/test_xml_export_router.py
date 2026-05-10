"""Contract tests for /api/clips/{id}/export.xml (W1.6)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipRecord, JobRecord
from app.db.session import get_session
from app.main import create_app


@pytest.fixture
async def export_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        job = JobRecord(youtube_url="https://example.com/v")
        session.add(job)
        await session.flush()
        rendered = ClipRecord(
            job_id=job.id, start=0, end=12.4, output_path="/tmp/clip.mp4",
            title="Best part",
        )
        unrendered = ClipRecord(job_id=job.id, start=0, end=10)
        session.add(rendered)
        session.add(unrendered)
        await session.commit()
        rid, uid = rendered.id, unrendered.id

    async def _override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, rid, uid

    await engine.dispose()


async def test_export_premiere_default(export_client):
    client, rid, _ = export_client
    r = await client.get(f"/api/clips/{rid}/export.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert f'filename="{rid}.xml"' in r.headers["content-disposition"]
    # Well-formed XML containing xmeml root.
    root = ET.fromstring(r.text)
    assert root.tag == "xmeml"


async def test_export_davinci(export_client):
    client, rid, _ = export_client
    r = await client.get(f"/api/clips/{rid}/export.xml?format=davinci")
    assert r.status_code == 200
    assert f'filename="{rid}.fcpxml"' in r.headers["content-disposition"]
    root = ET.fromstring(r.text)
    assert root.tag == "fcpxml"


async def test_export_unknown_format(export_client):
    client, rid, _ = export_client
    r = await client.get(f"/api/clips/{rid}/export.xml?format=avid")
    assert r.status_code == 422


async def test_export_unrendered_clip_409(export_client):
    client, _, uid = export_client
    r = await client.get(f"/api/clips/{uid}/export.xml")
    assert r.status_code == 409


async def test_export_unknown_clip_404(export_client):
    client, *_ = export_client
    r = await client.get("/api/clips/missing/export.xml")
    assert r.status_code == 404
