"""Contract tests for /api/clips/bulk-export.zip (W3.7)."""
from __future__ import annotations

import io
import zipfile

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipRecord, JobRecord
from app.db.session import get_session
from app.main import create_app


@pytest.fixture
async def export_client(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    mp4_a = tmp_path / "a.mp4"; mp4_a.write_bytes(b"video-a")
    jpg_a = tmp_path / "a.jpg"; jpg_a.write_bytes(b"thumb-a")
    mp4_b = tmp_path / "b.mp4"; mp4_b.write_bytes(b"video-b")

    async with factory() as session:
        job = JobRecord(youtube_url="https://x.test")
        session.add(job)
        await session.flush()
        a = ClipRecord(job_id=job.id, start=0, end=5,
                       output_path=str(mp4_a), thumbnail_path=str(jpg_a),
                       title="A", hashtags=["fun"])
        b = ClipRecord(job_id=job.id, start=5, end=10, output_path=str(mp4_b),
                       title="B")
        session.add_all([a, b])
        await session.commit()
        ids = [a.id, b.id]

    async def _override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, ids

    await engine.dispose()


async def test_bulk_export_returns_zip(export_client):
    client, ids = export_client
    q = "&".join(f"ids={i}" for i in ids)
    r = await client.get(f"/api/clips/bulk-export.zip?{q}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"

    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "manifest.csv" in names
    # Both clips' mp4s.
    assert sum(1 for n in names if n.endswith(".mp4")) == 2
    assert sum(1 for n in names if n.endswith(".jpg")) == 1
    manifest = z.read("manifest.csv").decode("utf-8")
    assert "clip_id" in manifest
    assert "fun" in manifest  # hashtag


async def test_bulk_export_no_ids_422(export_client):
    client, _ = export_client
    r = await client.get("/api/clips/bulk-export.zip")
    assert r.status_code == 422


async def test_bulk_export_unknown_404(export_client):
    client, _ = export_client
    r = await client.get("/api/clips/bulk-export.zip?ids=not-real")
    assert r.status_code == 404


async def test_bulk_export_too_many(export_client, monkeypatch):
    monkeypatch.setattr("app.settings.settings.bulk_export_max_clips", 1)
    client, ids = export_client
    q = "&".join(f"ids={i}" for i in ids)
    r = await client.get(f"/api/clips/bulk-export.zip?{q}")
    assert r.status_code == 422
