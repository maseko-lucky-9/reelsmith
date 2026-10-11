"""``GET /clips/{clip_id}`` serves one clip (T037, FR-031).

The publish page calls ``getClip`` (``GET /api/clips/{id}``) to prefill its
title, description and hashtags. The route did not exist, so the page always
got a 404. It returns the same item ``GET /clips`` returns for that clip, and
404s (``{"detail": "clip not found"}``, like the other clip routes) for an
unknown or retired clip.

Every test runs against both job stores: the in-memory one and ``SqlJobStore``
on an in-memory aiosqlite database. ``get_session`` is overridden with that
same database, so no test can reach the developer's ``reelsmith.db``.

The main hazard is route order: routers are matched in the order they are
included, ``clips`` before ``bulk_export``, so a plain ``/clips/{clip_id}``
would answer ``GET /clips/bulk-export.zip`` and the bulk export would never
run. ``test_bulk_export_path_*`` pin that.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bus.job_store import SqlJobStore
from app.db import models as _models  # noqa: F401 - registers the tables on Base
from app.db.base import Base
from app.db.session import get_session
from app.domain.models import JobState
from app.main import create_app
from app.settings import settings

KEY = "test-key"
JOB_ID = "job-1"
NOT_FOUND = {"detail": "clip not found"}
# What the bulk-export handler says when it runs and gets no ids.
BULK_NO_IDS = {"detail": "no clip ids"}

# Every field a stored clip carries (the SQL store's `_clip_record_to_dict`),
# i.e. everything the web `ClipRecord` type and `GET /clips` can show.
STORED_FIELDS = {
    "chapter_id": None,
    "start": 1.5,
    "end": 31.5,
    "output_path": "/tmp/reelsmith/c1.mp4",
    "thumbnail_path": "/tmp/reelsmith/c1.jpg",
    "title": "The one thing nobody tells you",
    "summary": "A short summary of the clip.",
    "hashtags": ["shorts", "#learning"],
    "virality_score": 87,
    "score_breakdown": {"hook": 0.9, "audio": 0.4},
    "transcript": {"text": "hello world", "words": []},
    "liked": True,
    "disliked": False,
    "retired": False,
    "ai_hook_text": "Wait until you see this",
    "ai_hook_audio_path": "/tmp/reelsmith/c1-hook.wav",
    "broll_assets": [
        {"id": "px-1", "author": "Ada", "url": "https://www.pexels.com/video/1/"}
    ],
    "caption_style": "karaoke",
    "captions_burnt_path": "/tmp/reelsmith/c1-captioned.mp4",
}
EXPECTED_KEYS = {"clip_id", "job_id", *STORED_FIELDS}


@dataclass
class Api:
    client: httpx.AsyncClient
    app: FastAPI
    kind: str

    @property
    def store(self) -> Any:
        return self.app.state.job_store

    async def seed(self, clip_id: str, *, retired: bool = False, **fields: Any) -> None:
        """Store a clip; ``retired`` goes through the store's own retire."""
        data = {**STORED_FIELDS, **fields}
        await self.store.upsert_clip(JOB_ID, clip_id, lambda c: c.update(data))
        if retired:
            assert await self.store.retire_clips(JOB_ID, [clip_id]) == 1


async def _build_app(kind: str) -> tuple[FastAPI, Any]:
    """An app on the ``kind`` store; returns it with the aiosqlite engine."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _session() -> AsyncIterator[Any]:
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.state._t037_factory = factory
    return app, engine


@pytest.fixture(params=["memory", "sql"])
async def api(request: pytest.FixtureRequest) -> AsyncIterator[Api]:
    kind: str = request.param
    app, engine = await _build_app(kind)
    async with LifespanManager(app):
        if kind == "sql":
            # Skip __init__: it builds an engine from YTVIDEO_DB_URL.
            sql_store = SqlJobStore.__new__(SqlJobStore)
            sql_store._factory = app.state._t037_factory
            app.state.job_store = sql_store
        await app.state.job_store.create(
            JobState(
                job_id=JOB_ID, url="https://yt.test/watch?v=abc", download_path="/tmp"
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            yield Api(client=client, app=app, kind=kind)
    await engine.dispose()


@pytest.fixture
async def secured(api: Api, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Api]:
    """The same store kinds, on an app built with ``require_auth=True``."""
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "api_key", KEY)
    app, engine = await _build_app(api.kind)
    async with LifespanManager(app):
        if api.kind == "sql":
            sql_store = SqlJobStore.__new__(SqlJobStore)
            sql_store._factory = app.state._t037_factory
            app.state.job_store = sql_store
        await app.state.job_store.create(
            JobState(
                job_id=JOB_ID, url="https://yt.test/watch?v=abc", download_path="/tmp"
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            yield Api(client=client, app=app, kind=api.kind)
    await engine.dispose()


@pytest.fixture
def media_files(tmp_path: Path) -> dict[str, str]:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 64)
    thumb = tmp_path / "thumb.jpg"
    thumb.write_bytes(b"\xff\xd8\xff")
    return {"output_path": str(video), "thumbnail_path": str(thumb)}


ADDRESSES = ["/clips", "/api/clips"]


# -- the clip ---------------------------------------------------------------


async def test_live_clip_comes_back_with_every_field(api: Api) -> None:
    await api.seed("c1")

    response = await api.client.get("/clips/c1")

    assert response.status_code == 200
    body = response.json()
    assert EXPECTED_KEYS <= set(body)
    assert body["clip_id"] == "c1"
    assert body["job_id"] == JOB_ID
    for field, value in STORED_FIELDS.items():
        assert body[field] == value, field


async def test_the_item_is_the_one_list_clips_returns(api: Api) -> None:
    await api.seed("c1")
    await api.seed("c2", title="another", liked=False)

    listed = {c["clip_id"]: c for c in (await api.client.get("/clips")).json()}
    one = await api.client.get("/clips/c2")

    assert one.status_code == 200
    assert one.json() == listed["c2"]


async def test_title_summary_hashtags_for_the_publish_page(api: Api) -> None:
    """The publish page reads exactly these three."""
    await api.seed("c1", title="T", summary="S", hashtags=["a", "b"])

    body = (await api.client.get("/clips/c1")).json()

    assert (body["title"], body["summary"], body["hashtags"]) == ("T", "S", ["a", "b"])


async def test_unknown_clip_is_404_with_the_clip_route_message(api: Api) -> None:
    response = await api.client.get("/clips/nope")

    assert response.status_code == 404
    # "Not Found" would mean the route is missing, not that the clip is.
    assert response.json() == NOT_FOUND


async def test_retired_clip_is_404_like_an_unknown_one(api: Api) -> None:
    await api.seed("old", retired=True)

    response = await api.client.get("/clips/old")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    # The clip is still stored: the 404 comes from the retired filter.
    assert await api.store.get_clip("old", include_retired=True) is not None
    assert "old" not in {c["clip_id"] for c in (await api.client.get("/clips")).json()}


async def test_a_live_clip_beside_a_retired_one_is_unaffected(api: Api) -> None:
    await api.seed("old", retired=True)
    await api.seed("c1")

    assert (await api.client.get("/clips/c1")).status_code == 200
    assert (await api.client.get("/clips/old")).status_code == 404


async def test_reflects_a_like_made_through_the_api(api: Api) -> None:
    await api.seed("c1", liked=False)

    assert (await api.client.patch("/clips/c1/like")).status_code == 200
    body = (await api.client.get("/clips/c1")).json()

    assert body["liked"] is True
    assert body["disliked"] is False


@pytest.mark.parametrize("prefix", ADDRESSES)
async def test_served_at_both_addresses(api: Api, prefix: str) -> None:
    """`/api/...` reaches the same route through ApiPrefixMiddleware (ADR-005)."""
    await api.seed("c1")

    found = await api.client.get(f"{prefix}/c1")
    missing = await api.client.get(f"{prefix}/nope")

    assert found.status_code == 200
    assert found.json()["clip_id"] == "c1"
    assert missing.status_code == 404
    assert missing.json() == NOT_FOUND


async def test_only_get_is_served_at_the_clip_path(api: Api) -> None:
    await api.seed("c1")

    for method in ("POST", "PUT", "DELETE", "PATCH"):
        response = await api.client.request(method, "/clips/c1")
        assert response.status_code == 405, method


# -- route order: the fixed `/clips/...` paths keep their handlers -----------


@pytest.mark.parametrize("prefix", ADDRESSES)
async def test_bulk_export_path_still_reaches_bulk_export(
    api: Api, prefix: str
) -> None:
    no_ids = await api.client.get(f"{prefix}/bulk-export.zip")
    unknown = await api.client.get(f"{prefix}/bulk-export.zip", params={"ids": "nope"})

    assert no_ids.status_code == 422
    assert no_ids.json() == BULK_NO_IDS
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "no clips matched"}


async def test_bulk_export_path_is_not_read_as_a_clip_id(api: Api) -> None:
    """Even a stored clip with that id does not capture the export route."""
    await api.seed("bulk-export.zip")

    response = await api.client.get("/clips/bulk-export.zip")

    assert response.status_code == 422
    assert response.json() == BULK_NO_IDS


async def test_openapi_lists_the_route_beside_the_fixed_paths(api: Api) -> None:
    paths = api.app.openapi()["paths"]

    assert "get" in paths["/clips/{clip_id}"]
    assert "get" in paths["/clips/bulk-export.zip"]
    assert "get" in paths["/clips"]


async def test_other_clip_routes_are_unaffected(
    api: Api, media_files: dict[str, str]
) -> None:
    await api.seed("c1", **media_files)

    video = await api.client.get("/clips/c1/video")
    thumb = await api.client.get("/clips/c1/thumbnail")
    xml_bad_format = await api.client.get(
        "/clips/c1/export.xml", params={"format": "x"}
    )
    listed = await api.client.get("/clips")
    liked = await api.client.patch("/clips/c1/dislike")
    rerender_unknown = await api.client.post("/clips/nope/rerender", json={})

    assert (video.status_code, video.content) == (200, b"\x00" * 64)
    assert thumb.status_code == 200
    assert xml_bad_format.status_code == 422
    assert "format must be one of" in xml_bad_format.json()["detail"]
    assert listed.status_code == 200
    assert [c["clip_id"] for c in listed.json()] == ["c1"]
    assert liked.status_code == 200
    assert liked.json()["disliked"] is True
    assert rerender_unknown.status_code == 404
    assert rerender_unknown.json() == NOT_FOUND


# -- auth parity (FR-060) -----------------------------------------------------


@pytest.mark.parametrize("prefix", ADDRESSES)
async def test_no_key_is_401_even_for_an_unknown_clip(
    secured: Api, prefix: str
) -> None:
    for clip_id in ("c1", "nope"):
        response = await secured.client.get(f"{prefix}/{clip_id}")
        assert response.status_code == 401, clip_id
        assert response.json() == {"detail": "Unauthorized"}


@pytest.mark.parametrize("prefix", ADDRESSES)
async def test_wrong_key_is_401(secured: Api, prefix: str) -> None:
    bearer = await secured.client.get(
        f"{prefix}/c1", headers={"Authorization": "Bearer wrong-key"}
    )
    token = await secured.client.get(f"{prefix}/c1", params={"token": "wrong-key"})

    assert bearer.status_code == 401
    assert token.status_code == 401


@pytest.mark.parametrize("prefix", ADDRESSES)
async def test_right_key_gets_the_clip_or_the_404(secured: Api, prefix: str) -> None:
    await secured.seed("c1")
    auth = {"Authorization": f"Bearer {KEY}"}

    by_bearer = await secured.client.get(f"{prefix}/c1", headers=auth)
    by_token = await secured.client.get(f"{prefix}/c1", params={"token": KEY})
    unknown = await secured.client.get(f"{prefix}/nope", headers=auth)

    assert by_bearer.status_code == 200
    assert by_bearer.json()["clip_id"] == "c1"
    assert by_token.status_code == 200
    assert unknown.status_code == 404
    assert unknown.json() == NOT_FOUND


@pytest.fixture(autouse=True)
def _guard_real_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fail loudly if a request ever opens the app's own database."""

    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("test reached the real session factory")

    monkeypatch.setattr("app.db.session.get_session_factory", _boom)
    yield
