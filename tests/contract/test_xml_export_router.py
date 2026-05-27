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


@pytest.fixture
async def export_client_with_captions():
    """Fixture that provides a client with a clip that has captions_burnt_path set."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        job = JobRecord(youtube_url="https://example.com/v")
        session.add(job)
        await session.flush()
        clip_with_captions = ClipRecord(
            job_id=job.id,
            start=0,
            end=15.0,
            output_path="/tmp/clip_captions.mp4",
            title="Captioned clip",
            captions_burnt_path="/tmp/clip_captions_burnt.mp4",
        )
        clip_no_captions = ClipRecord(
            job_id=job.id,
            start=0,
            end=10.0,
            output_path="/tmp/clip_plain.mp4",
            title="Plain clip",
        )
        session.add(clip_with_captions)
        session.add(clip_no_captions)
        await session.commit()
        cid = clip_with_captions.id
        pid = clip_no_captions.id

    async def _override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, cid, pid

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


async def test_export_davinci_multitrack_with_captions(export_client_with_captions):
    """DaVinci export for a clip with captions_burnt_path must contain two <spine> entries."""
    client, cid, _ = export_client_with_captions
    r = await client.get(f"/api/clips/{cid}/export.xml?format=davinci")
    assert r.status_code == 200
    assert f'filename="{cid}.fcpxml"' in r.headers["content-disposition"]
    # Count <spine> tags in the raw body (namespace-free approach)
    spine_count = r.text.count("<spine>")
    assert spine_count == 2, f"expected 2 <spine> entries, got {spine_count}"
    # Both the main clip and the captions asset reference should appear
    assert "/tmp/clip_captions.mp4" in r.text
    assert "/tmp/clip_captions_burnt.mp4" in r.text


async def test_export_davinci_single_track_without_captions(export_client_with_captions):
    """DaVinci export for a clip without captions_burnt_path must contain exactly one <spine>."""
    client, _, pid = export_client_with_captions
    r = await client.get(f"/api/clips/{pid}/export.xml?format=davinci")
    assert r.status_code == 200
    spine_count = r.text.count("<spine>")
    assert spine_count == 1, f"expected 1 <spine> entry, got {spine_count}"
    assert "/tmp/clip_plain.mp4" in r.text
