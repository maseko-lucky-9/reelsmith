"""Contract tests for ``/api/clips/{clip_id}/edit`` (W1.2).

Hermetic: in-memory SQLite engine with dependency_overrides; no shared
state across tests.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipRecord, JobRecord
from app.db.session import get_session
from app.main import create_app


@pytest.fixture
async def edit_client_with_clip():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        job = JobRecord(youtube_url="https://example.com/v")
        session.add(job)
        await session.flush()
        clip = ClipRecord(job_id=job.id, start=0.0, end=10.0)
        session.add(clip)
        await session.commit()
        clip_id = clip.id

    async def _override():
        async with factory() as session:
            yield session

    app_inst = create_app()
    app_inst.dependency_overrides[get_session] = _override

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_inst), base_url="http://test"
    ) as client:
        yield client, clip_id

    await engine.dispose()


def _timeline(text: str = "hook") -> dict[str, Any]:
    return {
        "tracks": [
            {"kind": "video", "items": [{"start": 0.0, "end": 10.0, "src": "main"}]},
            {"kind": "caption", "items": []},
            {
                "kind": "text-overlay",
                "items": [{"start": 1.0, "end": 4.0, "text": text, "x": 0.5, "y": 0.1}],
            },
        ]
    }


async def test_get_edit_404_when_no_state(edit_client_with_clip) -> None:
    client, clip_id = edit_client_with_clip
    r = await client.get(f"/api/clips/{clip_id}/edit")
    assert r.status_code == 404


async def test_get_edit_404_when_clip_missing(edit_client_with_clip) -> None:
    client, _ = edit_client_with_clip
    r = await client.get("/api/clips/does-not-exist/edit")
    assert r.status_code == 404


async def test_create_then_read_edit(edit_client_with_clip) -> None:
    client, clip_id = edit_client_with_clip
    r = await client.put(
        f"/api/clips/{clip_id}/edit",
        json={"timeline": _timeline()},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["clip_id"] == clip_id
    assert body["version"] == 1
    assert body["timeline"]["tracks"][0]["kind"] == "video"

    r = await client.get(f"/api/clips/{clip_id}/edit")
    assert r.status_code == 200
    assert r.json()["version"] == 1


async def test_update_increments_version(edit_client_with_clip) -> None:
    client, clip_id = edit_client_with_clip
    r1 = await client.put(f"/api/clips/{clip_id}/edit", json={"timeline": _timeline("v1")})
    assert r1.json()["version"] == 1

    r2 = await client.put(
        f"/api/clips/{clip_id}/edit",
        json={"timeline": _timeline("v2"), "version": 1},
    )
    assert r2.status_code == 200
    assert r2.json()["version"] == 2
    assert r2.json()["timeline"]["tracks"][2]["items"][0]["text"] == "v2"


async def test_version_mismatch_returns_409(edit_client_with_clip) -> None:
    client, clip_id = edit_client_with_clip
    await client.put(f"/api/clips/{clip_id}/edit", json={"timeline": _timeline()})
    # Server now at version 1; client claims version 5 → conflict.
    r = await client.put(
        f"/api/clips/{clip_id}/edit",
        json={"timeline": _timeline("late"), "version": 5},
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["server_version"] == 1
    assert detail["client_version"] == 5


async def test_omitted_version_force_overwrites(edit_client_with_clip) -> None:
    client, clip_id = edit_client_with_clip
    await client.put(f"/api/clips/{clip_id}/edit", json={"timeline": _timeline("v1")})
    # Omitting version is treated as a force-save (still bumps).
    r = await client.put(f"/api/clips/{clip_id}/edit", json={"timeline": _timeline("v2")})
    assert r.status_code == 200
    assert r.json()["version"] == 2


async def test_unknown_track_kind_rejected(edit_client_with_clip) -> None:
    client, clip_id = edit_client_with_clip
    r = await client.put(
        f"/api/clips/{clip_id}/edit",
        json={
            "timeline": {
                "tracks": [{"kind": "audio-fx", "items": []}]
            }
        },
    )
    # Pydantic Literal[…] discriminator rejects with 422.
    assert r.status_code == 422


async def test_delete_edit_idempotent(edit_client_with_clip) -> None:
    client, clip_id = edit_client_with_clip
    # Delete with no edit state — must be 204, not 404.
    r1 = await client.delete(f"/api/clips/{clip_id}/edit")
    assert r1.status_code == 204

    await client.put(f"/api/clips/{clip_id}/edit", json={"timeline": _timeline()})
    r2 = await client.delete(f"/api/clips/{clip_id}/edit")
    assert r2.status_code == 204

    r3 = await client.get(f"/api/clips/{clip_id}/edit")
    assert r3.status_code == 404


async def test_put_against_missing_clip_404(edit_client_with_clip) -> None:
    client, _ = edit_client_with_clip
    r = await client.put(
        "/api/clips/does-not-exist/edit",
        json={"timeline": _timeline()},
    )
    assert r.status_code == 404
