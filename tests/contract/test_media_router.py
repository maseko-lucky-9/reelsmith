from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import create_app


def test_get_video_unknown_clip_returns_404():
    with TestClient(create_app()) as client:
        response = client.get("/clips/nonexistent/video")
    assert response.status_code == 404


def test_get_thumbnail_unknown_clip_returns_404():
    with TestClient(create_app()) as client:
        response = client.get("/clips/nonexistent/thumbnail")
    assert response.status_code == 404


def test_get_video_clip_with_missing_file_returns_404(tmp_path):
    with TestClient(create_app()) as client:
        with patch.object(
            client.app.state.job_store,
            "list_clips",
            new=AsyncMock(return_value=[{
                "clip_id": "clip-abc",
                "output_path": str(tmp_path / "nonexistent.mp4"),
            }])
        ):
            response = client.get("/clips/clip-abc/video")
    assert response.status_code == 404


def test_get_video_existing_clip_returns_200(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 512)

    with TestClient(create_app()) as client:
        with patch.object(
            client.app.state.job_store,
            "list_clips",
            new=AsyncMock(return_value=[{
                "clip_id": "clip-abc",
                "output_path": str(video),
            }])
        ):
            response = client.get("/clips/clip-abc/video")
    assert response.status_code == 200
    assert "video/mp4" in response.headers["content-type"]


def test_get_video_range_request_returns_206(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 512)

    with TestClient(create_app()) as client:
        with patch.object(
            client.app.state.job_store,
            "list_clips",
            new=AsyncMock(return_value=[{
                "clip_id": "clip-abc",
                "output_path": str(video),
            }])
        ):
            response = client.get(
                "/clips/clip-abc/video",
                headers={"Range": "bytes=0-255"},
            )
    assert response.status_code == 206
    assert response.headers["content-range"].startswith("bytes 0-255/512")


def test_get_thumbnail_existing_clip_returns_jpeg(tmp_path):
    thumb = tmp_path / "thumb.jpg"
    thumb.write_bytes(b"\xff\xd8\xff")

    with TestClient(create_app()) as client:
        with patch.object(
            client.app.state.job_store,
            "list_clips",
            new=AsyncMock(return_value=[{
                "clip_id": "clip-abc",
                "thumbnail_path": str(thumb),
            }])
        ):
            response = client.get("/clips/clip-abc/thumbnail")
    assert response.status_code == 200
    assert "image/jpeg" in response.headers["content-type"]
