from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app


def test_post_render_invalid_range_returns_422(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    with TestClient(create_app()) as client:
        response = client.post(
            "/renders",
            json={
                "video_path": str(video),
                "output_path": str(tmp_path / "o.mp4"),
                "start": 5,
                "end": 1,
            },
        )
    assert response.status_code == 422


def test_post_render_missing_video_returns_404(tmp_path):
    with TestClient(create_app()) as client:
        response = client.post(
            "/renders",
            json={
                "video_path": "/nope.mp4",
                "output_path": str(tmp_path / "o.mp4"),
                "start": 0,
                "end": 1,
            },
        )
    assert response.status_code == 404


@patch("app.routers.renders.render_clip", return_value="/tmp/o.mp4")
def test_post_render_invokes_service(mock_render, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    with TestClient(create_app()) as client:
        response = client.post(
            "/renders",
            json={
                "video_path": str(video),
                "output_path": str(tmp_path / "o.mp4"),
                "start": 0,
                "end": 5,
            },
        )
    assert response.status_code == 200
    assert response.json()["output_path"] == "/tmp/o.mp4"
    mock_render.assert_called_once()
