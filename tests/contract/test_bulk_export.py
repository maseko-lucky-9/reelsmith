"""Contract tests for /api/clips/bulk-export.zip (W3.7)."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import tempfile
import threading
import zipfile
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipRecord, JobRecord
from app.db.session import get_session
from app.main import create_app
from app.routers import bulk_export as bulk_export_router

# B-roll files live under the server's cache and library dirs; the manifest
# must credit them without naming those paths.
_SERVER_BROLL_DIR = "/var/reelsmith-test/broll"
_PEXELS_ASSET = {
    "query": "ocean",
    "start": 3.0,
    "duration": 3.0,
    "provider": "pexels",
    "asset_id": "1234",
    # A comma and quotes, so the CSV cell must be quoted and escaped.
    "author": 'Jane "JD" Doe, Studio',
    "source_url": "https://www.pexels.com/video/ocean-1234/",
    "path": f"{_SERVER_BROLL_DIR}/cache/videos/1234.mp4",
}
_LOCAL_ASSET = {
    "query": "forest",
    "start": 7.0,
    "duration": 3.0,
    "provider": "local",
    "asset_id": "forest.mp4",
    "author": "",
    "source_url": "",
    "path": f"{_SERVER_BROLL_DIR}/library/forest.mp4",
}
_MANIFEST_COLUMNS = [
    "clip_id", "title", "summary", "start", "end", "output_path",
    "thumbnail_path", "virality_score", "hashtags",
]  # fmt: skip


@pytest.fixture
async def export_client(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    mp4_a = tmp_path / "a.mp4"
    mp4_a.write_bytes(b"video-a")
    jpg_a = tmp_path / "a.jpg"
    jpg_a.write_bytes(b"thumb-a")
    mp4_b = tmp_path / "b.mp4"
    mp4_b.write_bytes(b"video-b")
    # Exists on disk so a leaked retired clip would show up in the zip too.
    mp4_c = tmp_path / "c.mp4"
    mp4_c.write_bytes(b"video-c")

    async with factory() as session:
        job = JobRecord(youtube_url="https://x.test")
        session.add(job)
        await session.flush()
        a = ClipRecord(
            job_id=job.id,
            start=0,
            end=5,
            output_path=str(mp4_a),
            thumbnail_path=str(jpg_a),
            title="A",
            hashtags=["fun"],
            # The Pexels asset is inserted twice; credits list it once.
            broll_assets=[_PEXELS_ASSET, _LOCAL_ASSET, {**_PEXELS_ASSET, "start": 9.0}],
        )
        b = ClipRecord(
            job_id=job.id, start=5, end=10, output_path=str(mp4_b), title="B"
        )
        # A retired clip: the retention sweep keeps the row but deletes its files.
        c = ClipRecord(
            job_id=job.id,
            start=10,
            end=15,
            title="C",
            retired=True,
            output_path=str(mp4_c),
        )
        session.add_all([a, b, c])
        await session.commit()
        ids = [a.id, b.id]
        retired_id = c.id

    async def _override():
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.retired_id = retired_id  # type: ignore[attr-defined]  # test-only handle
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


async def _manifest_rows(client, ids) -> tuple[list[str], dict[str, dict[str, str]]]:
    """The zip manifest's header and its rows keyed by clip id."""
    q = "&".join(f"ids={i}" for i in ids)
    r = await client.get(f"/api/clips/bulk-export.zip?{q}")
    assert r.status_code == 200
    text = zipfile.ZipFile(io.BytesIO(r.content)).read("manifest.csv").decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    rows = {row["clip_id"]: row for row in reader}
    return list(reader.fieldnames or []), rows


async def test_bulk_manifest_appends_broll_credits_after_the_existing_columns(
    export_client,
):
    client, ids = export_client

    header, _rows = await _manifest_rows(client, ids)

    assert header == [*_MANIFEST_COLUMNS, "broll_credits"]


async def test_bulk_manifest_credits_each_distinct_broll_asset(export_client):
    client, ids = export_client

    _header, rows = await _manifest_rows(client, ids)

    assert json.loads(rows[ids[0]]["broll_credits"]) == [
        {
            "provider": "pexels",
            "author": 'Jane "JD" Doe, Studio',
            "source_url": "https://www.pexels.com/video/ocean-1234/",
        },
        {"provider": "local", "author": "", "source_url": ""},
    ]
    # The other columns of the row are unchanged.
    assert rows[ids[0]]["title"] == "A"
    assert rows[ids[0]]["hashtags"] == "fun"


async def test_bulk_manifest_has_no_credits_for_a_clip_without_broll(export_client):
    client, ids = export_client

    _header, rows = await _manifest_rows(client, ids)

    assert json.loads(rows[ids[1]]["broll_credits"]) == []


async def test_bulk_manifest_does_not_name_server_broll_paths(export_client):
    client, ids = export_client
    q = "&".join(f"ids={i}" for i in ids)

    r = await client.get(f"/api/clips/bulk-export.zip?{q}")

    manifest = zipfile.ZipFile(io.BytesIO(r.content)).read("manifest.csv").decode()
    assert _SERVER_BROLL_DIR not in manifest
    assert "forest.mp4" not in manifest


async def test_bulk_export_skips_retired_clips(export_client):
    client, ids = export_client
    retired_id = client.retired_id
    q = "&".join(f"ids={i}" for i in [*ids, retired_id])
    r = await client.get(f"/api/clips/bulk-export.zip?{q}")
    assert r.status_code == 200

    z = zipfile.ZipFile(io.BytesIO(r.content))
    manifest = z.read("manifest.csv").decode()
    assert retired_id not in manifest
    assert f"clips/{retired_id}.mp4" not in z.namelist()
    # The live clips are still exported.
    assert all(i in manifest for i in ids)


async def test_bulk_export_only_retired_is_404(export_client):
    client, _ = export_client
    r = await client.get(f"/api/clips/bulk-export.zip?ids={client.retired_id}")
    assert r.status_code == 404


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


# ── Streaming from a temp file (P4) ──────────────────────────────────────────
# Invariants: the temp file never has a name on disk (link count 0 from
# creation, so nothing can be left behind), and its handle is closed however
# the response ends — fully read, iterator closed, or client disconnect.


@pytest.fixture
def temp_files(monkeypatch):
    """Record every temp file opened (named or not) with its link count at
    creation."""
    opened: list[tuple[Any, int]] = []

    def spying(factory):
        def spy(*args, **kwargs):
            handle = factory(*args, **kwargs)
            opened.append((handle, os.fstat(handle.fileno()).st_nlink))
            return handle

        return spy

    for name in ("TemporaryFile", "NamedTemporaryFile"):
        monkeypatch.setattr(tempfile, name, spying(getattr(tempfile, name)))
    return opened


async def test_bulk_export_streams_stored_zip_from_unlinked_temp_file(
    export_client, temp_files
):
    client, ids = export_client
    q = "&".join(f"ids={i}" for i in ids)

    r = await client.get(f"/api/clips/bulk-export.zip?{q}")

    assert r.status_code == 200
    assert r.headers["content-length"] == str(len(r.content))
    assert r.headers["content-disposition"] == (
        'attachment; filename="reelsmith-bulk-export.zip"'
    )
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert z.testzip() is None
    assert all(info.compress_type == zipfile.ZIP_STORED for info in z.infolist())
    assert sorted(z.read(n) for n in z.namelist() if n.endswith(".mp4")) == [
        b"video-a",
        b"video-b",
    ]
    [(handle, links_at_creation)] = temp_files
    assert links_at_creation == 0, "temp file must have no name on disk"
    assert handle.closed


async def test_bulk_export_builds_zip_off_the_event_loop(export_client, monkeypatch):
    client, ids = export_client
    threads: list[threading.Thread] = []
    real = bulk_export_router._write_zip

    def spy(*args, **kwargs):
        threads.append(threading.current_thread())
        return real(*args, **kwargs)

    monkeypatch.setattr(bulk_export_router, "_write_zip", spy)
    loop_thread = threading.current_thread()

    r = await client.get(f"/api/clips/bulk-export.zip?ids={ids[0]}")

    assert r.status_code == 200
    assert len(threads) == 1
    assert threads[0] is not loop_thread


async def test_bulk_export_closes_temp_file_when_body_iterator_is_closed(
    export_client, temp_files, monkeypatch
):
    """Read one chunk, then close the body iterator (what a server does when
    it abandons a response)."""
    client, ids = export_client
    monkeypatch.setattr(bulk_export_router, "_STREAM_CHUNK_BYTES", 16)
    session_override = client._transport.app.dependency_overrides[get_session]

    async for session in session_override():
        response = await bulk_export_router.bulk_export(ids=ids, session=session)
    body = response.body_iterator
    first = await body.__anext__()
    [(handle, _links)] = temp_files
    assert len(first) == 16
    assert not handle.closed
    assert os.fstat(handle.fileno()).st_nlink == 0

    await body.aclose()

    assert handle.closed


async def test_bulk_export_closes_temp_file_on_client_disconnect(
    export_client, temp_files, monkeypatch
):
    """Drive the ASGI app directly; the client disconnects after the first
    body chunk, mid-stream."""
    client, ids = export_client
    monkeypatch.setattr(bulk_export_router, "_STREAM_CHUNK_BYTES", 16)
    app = client._transport.app
    first_chunk_sent = asyncio.Event()
    sent: list[dict[str, Any]] = []
    request_delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal request_delivered
        if not request_delivered:
            request_delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await first_chunk_sent.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            first_chunk_sent.set()
            await asyncio.sleep(0.05)  # a slow client: the disconnect wins

    query = "&".join(f"ids={i}" for i in ids).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/clips/bulk-export.zip",
        "raw_path": b"/api/clips/bulk-export.zip",
        "query_string": query,
        "root_path": "",
        "headers": [(b"host", b"test")],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }

    await asyncio.wait_for(app(scope, receive, send), timeout=5)

    start = sent[0]
    assert start["status"] == 200
    declared = int(dict(start["headers"])[b"content-length"])
    streamed = sum(
        len(m.get("body", b"")) for m in sent if m["type"] == "http.response.body"
    )
    assert 0 < streamed < declared, "the response must have been cut short"
    [(handle, _links)] = temp_files
    assert handle.closed


async def test_bulk_export_rejections_create_no_temp_file(export_client, temp_files):
    client, _ids = export_client

    assert (await client.get("/api/clips/bulk-export.zip")).status_code == 422
    assert (
        await client.get("/api/clips/bulk-export.zip?ids=not-real")
    ).status_code == 404
    assert temp_files == []
