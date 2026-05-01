from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from app.main import create_app


@patch("app.services.folder_service.YoutubeDL")
def test_post_folder_creates_directories(mock_ydl_cls, tmp_path):
    instance = MagicMock()
    instance.__enter__.return_value.extract_info.return_value = {"title": "My Video"}
    mock_ydl_cls.return_value = instance

    with TestClient(create_app()) as client:
        response = client.post(
            "/folders",
            json={"download_path": str(tmp_path), "url": "https://example.com/v"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["destination_folder"].endswith("My_Video")
    assert data["clips_folder"].endswith("clips")
