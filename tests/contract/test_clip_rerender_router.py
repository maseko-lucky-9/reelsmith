"""``POST /clips/{id}/rerender`` validates before it queues a re-render (FR-015).

Order: unknown/retired clip -> 404; job missing or not completed -> 409;
source video not retained -> 409; otherwise 202 and one queued item that the
orchestrator dispatches to the single-clip re-render.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain.models import JobState, PipelineOptions
from app.main import create_app

JOB_URL = "https://www.youtube.com/watch?v=rerender001"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        # Nothing consumes this queue, so nothing runs; the test inspects it.
        test_client.app.state.job_queue = asyncio.Queue()
        yield test_client


@pytest.fixture
def source_video(tmp_path: Path) -> Path:
    video = tmp_path / "source.mp4"
    video.write_bytes(b"\x00" * 64)
    return video


def _seed(
    client: TestClient,
    *,
    status: str = "completed",
    video_path: str | None = None,
    with_job: bool = True,
) -> None:
    store = client.app.state.job_store
    if with_job:
        client.portal.call(
            store.create,
            JobState(
                job_id="job-1",
                url=JOB_URL,
                download_path="/downloads",
                caption_format="vtt",
                language="fr-FR",
                pipeline_options=PipelineOptions(ai_hook=True),
            ),
        )

        def _mutate(s: JobState) -> None:
            s.status = status  # type: ignore[assignment]
            s.video_path = video_path

        client.portal.call(store.update, "job-1", _mutate)
    client.portal.call(
        store.upsert_clip,
        "job-1",
        "c1",
        lambda c: c.update(
            {"start": 1.0, "end": 5.0, "output_path": "/clips/00_x.mp4"}
        ),
    )


def _queue(client: TestClient) -> asyncio.Queue[tuple[str, dict[str, Any]]]:
    return client.app.state.job_queue


def test_completed_job_with_source_queues_one_rerender(client, source_video):
    _seed(client, video_path=str(source_video))

    response = client.post(
        "/clips/c1/rerender", json={"reframe_provider": "face_track"}
    )

    assert response.status_code == 202
    assert response.json() == {"status": "queued", "clip_id": "c1"}
    assert _queue(client).qsize() == 1
    job_id, payload = _queue(client).get_nowait()
    assert job_id == "job-1"
    assert payload["rerender_clip_id"] == "c1"
    assert payload["url"] == JOB_URL
    assert payload["download_path"] == "/downloads"
    assert payload["caption_format"] == "vtt"
    assert payload["language"] == "fr-FR"
    assert payload["pipeline_options"]["ai_hook"] is True
    assert payload["reframe_provider"] == "face_track"


@pytest.mark.parametrize("status", ["pending", "running", "failed"])
def test_job_not_completed_is_409(client, source_video, status):
    _seed(client, status=status, video_path=str(source_video))

    response = client.post("/clips/c1/rerender", json={})

    assert response.status_code == 409
    assert "completed" in response.json()["detail"]
    assert _queue(client).qsize() == 0


def test_job_missing_is_409(client):
    _seed(client, with_job=False)

    response = client.post("/clips/c1/rerender", json={})

    assert response.status_code == 409
    assert _queue(client).qsize() == 0


def test_source_file_gone_is_409(client, tmp_path):
    _seed(client, video_path=str(tmp_path / "deleted.mp4"))

    response = client.post("/clips/c1/rerender", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == "source video not retained"
    assert _queue(client).qsize() == 0


def test_job_without_recorded_source_is_409(client):
    _seed(client, video_path=None)

    response = client.post("/clips/c1/rerender", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == "source video not retained"
    assert _queue(client).qsize() == 0


def test_unknown_clip_is_404_and_nothing_queued(client, source_video):
    _seed(client, video_path=str(source_video))

    response = client.post("/clips/nope/rerender", json={})

    assert response.status_code == 404
    assert _queue(client).qsize() == 0
