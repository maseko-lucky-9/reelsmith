"""Routes that look up one job/clip use targeted store queries.

* ``POST /jobs`` finds a duplicate URL with ``find_job_by_url`` (exact match,
  any age) instead of scanning ``list_jobs(limit=200)``.
* ``/clips/{id}/video``, ``/clips/{id}/thumbnail`` and ``/clips/{id}/rerender``
  use ``get_clip`` instead of a ``list_clips()`` full scan, and still 404 on
  retired clips (``list_clips`` hides them).
"""

from __future__ import annotations

import asyncio
import functools
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.domain.models import JobState
from app.main import create_app

URL = "https://www.youtube.com/watch?v=dupdupdup01"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        # Nothing consumes this queue, so accepted jobs never start a pipeline.
        test_client.app.state.job_queue = asyncio.Queue()
        yield test_client


def _store(client: TestClient) -> Any:
    return client.app.state.job_store


def _spy_on_scans(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> dict[str, AsyncMock]:
    store = _store(client)
    spies = {
        "list_jobs": AsyncMock(wraps=store.list_jobs),
        "list_clips": AsyncMock(wraps=store.list_clips),
    }
    for name, spy in spies.items():
        monkeypatch.setattr(store, name, spy)
    return spies


def _add_job(client: TestClient, job_id: str, url: str, status: str) -> None:
    store = _store(client)
    client.portal.call(
        store.create, JobState(job_id=job_id, url=url, download_path="/tmp")
    )
    client.portal.call(store.update, job_id, lambda s: setattr(s, "status", status))


def _add_clip(
    client: TestClient, clip_id: str, *, retired: bool = False, **fields: Any
) -> None:
    store = _store(client)
    client.portal.call(store.upsert_clip, "job-1", clip_id, lambda c: c.update(fields))
    if retired:
        store._clips[clip_id]["retired"] = True


# ── POST /jobs duplicate check ────────────────────────────────────────────────


def test_duplicate_url_found_beyond_newest_200_jobs(client, monkeypatch):
    for i in range(205):
        _add_job(
            client,
            f"filler-{i}",
            f"https://www.youtube.com/watch?v=f{i:09d}",
            "completed",
        )
    _add_job(client, "the-original", URL, "completed")
    spies = _spy_on_scans(client, monkeypatch)

    response = client.post("/jobs", json={"url": URL, "download_path": "/tmp/x"})

    assert response.status_code == 202
    assert response.json() == {"job_id": "the-original", "status": "completed"}
    spies["list_jobs"].assert_not_called()


def test_failed_job_with_same_url_is_not_a_duplicate(client, monkeypatch):
    _add_job(client, "failed-one", URL, "failed")
    spies = _spy_on_scans(client, monkeypatch)

    response = client.post("/jobs", json={"url": URL, "download_path": "/tmp/x"})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["job_id"] != "failed-one"
    assert client.app.state.job_queue.qsize() == 1
    spies["list_jobs"].assert_not_called()


def test_url_that_only_contains_an_existing_url_is_not_a_duplicate(client):
    _add_job(client, "longer", URL + "&t=42", "running")

    response = client.post("/jobs", json={"url": URL, "download_path": "/tmp/x"})

    assert response.json()["status"] == "accepted"


# ── Clip media + rerender ─────────────────────────────────────────────────────


@pytest.fixture
def media_files(tmp_path: Path) -> dict[str, str]:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 512)
    thumb = tmp_path / "thumb.jpg"
    thumb.write_bytes(b"\xff\xd8\xff")
    return {"output_path": str(video), "thumbnail_path": str(thumb)}


def test_video_served_without_scanning_clips(client, monkeypatch, media_files):
    _add_clip(client, "c1", **media_files)
    spies = _spy_on_scans(client, monkeypatch)

    response = client.get("/clips/c1/video")

    assert response.status_code == 200
    assert response.content == b"\x00" * 512
    spies["list_clips"].assert_not_called()


def test_video_range_request_still_partial(client, media_files):
    _add_clip(client, "c1", **media_files)

    response = client.get("/clips/c1/video", headers={"Range": "bytes=0-255"})

    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-255/512"


def test_thumbnail_served_without_scanning_clips(client, monkeypatch, media_files):
    _add_clip(client, "c1", **media_files)
    spies = _spy_on_scans(client, monkeypatch)

    response = client.get("/clips/c1/thumbnail")

    assert response.status_code == 200
    assert "image/jpeg" in response.headers["content-type"]
    spies["list_clips"].assert_not_called()


@pytest.mark.parametrize("route", ["video", "thumbnail"])
def test_retired_clip_media_is_404(client, media_files, route):
    _add_clip(client, "old", retired=True, **media_files)

    response = client.get(f"/clips/old/{route}")

    assert response.status_code == 404


@pytest.mark.parametrize("route", ["video", "thumbnail"])
def test_unknown_clip_media_is_404(client, route):
    assert client.get(f"/clips/nope/{route}").status_code == 404


def test_rerender_queues_job_without_scanning_clips(client, monkeypatch, media_files):
    _add_clip(client, "c1", **media_files)
    spies = _spy_on_scans(client, monkeypatch)

    response = client.post(
        "/clips/c1/rerender", json={"reframe_provider": "face_track"}
    )

    assert response.status_code == 202
    assert response.json() == {"status": "queued", "clip_id": "c1"}
    job_id, payload = client.app.state.job_queue.get_nowait()
    assert job_id == "job-1"
    assert payload["rerender_clip_id"] == "c1"
    assert payload["reframe_provider"] == "face_track"
    spies["list_clips"].assert_not_called()


def test_rerender_retired_clip_is_404(client, media_files):
    _add_clip(client, "old", retired=True, **media_files)

    response = client.post("/clips/old/rerender", json={})

    assert response.status_code == 404
    assert client.app.state.job_queue.qsize() == 0


def test_rerender_unknown_clip_is_404(client):
    assert client.post("/clips/nope/rerender", json={}).status_code == 404


@pytest.mark.parametrize("action", ["like", "dislike"])
def test_like_dislike_still_resolve_retired_clips(client, media_files, action):
    """Unchanged behaviour: like/dislike looked retired clips up before P4."""
    _add_clip(client, "old", retired=True, **media_files)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        response = client.patch(f"/clips/old/{action}")

    assert response.status_code == 200
    assert response.json()["clip_id"] == "old"
    stored = client.portal.call(
        functools.partial(_store(client).get_clip, "old", include_retired=True)
    )
    assert stored is not None
    assert stored[f"{action}d"] is True


# ── Like / dislike persist (FR-014) ───────────────────────────────────────────


def _listed_clip(client: TestClient, clip_id: str) -> dict[str, Any]:
    clips = client.get("/clips").json()
    return next(c for c in clips if c["clip_id"] == clip_id)


def test_like_persists_on_next_read(client, media_files, recwarn):
    _add_clip(client, "c1", **media_files)

    response = client.patch("/clips/c1/like")

    assert response.status_code == 200
    clip = _listed_clip(client, "c1")
    assert clip["liked"] is True
    assert clip["disliked"] is False
    assert not [w for w in recwarn if issubclass(w.category, RuntimeWarning)]


def test_dislike_persists_on_next_read(client, media_files):
    _add_clip(client, "c1", **media_files)

    client.patch("/clips/c1/dislike")

    clip = _listed_clip(client, "c1")
    assert clip["disliked"] is True
    assert not clip.get("liked")


def test_like_twice_toggles_back_off(client, media_files):
    _add_clip(client, "c1", **media_files)

    client.patch("/clips/c1/like")
    second = client.patch("/clips/c1/like")

    assert second.json()["liked"] is False
    assert _listed_clip(client, "c1")["liked"] is False


def test_like_after_dislike_clears_dislike(client, media_files):
    _add_clip(client, "c1", **media_files)

    client.patch("/clips/c1/dislike")
    client.patch("/clips/c1/like")

    clip = _listed_clip(client, "c1")
    assert clip["liked"] is True
    assert clip["disliked"] is False


def test_dislike_after_like_clears_like(client, media_files):
    _add_clip(client, "c1", **media_files)

    client.patch("/clips/c1/like")
    client.patch("/clips/c1/dislike")

    clip = _listed_clip(client, "c1")
    assert clip["disliked"] is True
    assert clip["liked"] is False


def test_queued_job_with_same_url_is_a_duplicate(client):
    _add_job(client, "queued", URL, "pending")

    response = client.post("/jobs", json={"url": URL, "download_path": "/tmp/x"})

    assert response.json() == {"job_id": "queued", "status": "pending"}
    assert client.app.state.job_queue.qsize() == 0
