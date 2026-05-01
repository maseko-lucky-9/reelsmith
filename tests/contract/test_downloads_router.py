from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app


@patch("app.routers.downloads.download_video", return_value=(None, None))
def test_post_download_failure_returns_502(mock_dl):
    with TestClient(create_app()) as client:
        response = client.post(
            "/downloads",
            json={"url": "https://example.com/v", "destination_folder": "/tmp/x"},
        )
    assert response.status_code == 502


@patch("app.routers.downloads.download_video")
def test_post_download_success(mock_dl, tmp_path):
    info = {
        "title": "Demo",
        "duration": 120.0,
        "chapters": [
            {"title": "Intro", "start_time": 0.0, "end_time": 60.0},
            {"title": "Outro", "start_time": 60.0, "end_time": 120.0},
        ],
    }
    mock_dl.return_value = (str(tmp_path / "demo.mp4"), info)

    with TestClient(create_app()) as client:
        response = client.post(
            "/downloads",
            json={"url": "https://example.com/v", "destination_folder": str(tmp_path)},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Demo"
    assert data["duration"] == 120.0
    assert len(data["chapters"]) == 2
    assert data["chapters"][0]["index"] == 0
