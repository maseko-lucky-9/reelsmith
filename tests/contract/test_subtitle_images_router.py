from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_post_subtitle_image_writes_png(tmp_path):
    out = tmp_path / "subtitle.png"
    with TestClient(create_app()) as client:
        response = client.post(
            "/subtitle-images",
            json={"text": "hello", "width": 320, "height": 180, "output_path": str(out)},
        )
    assert response.status_code == 200
    assert response.json()["image_path"] == str(out)
    assert out.is_file() and out.stat().st_size > 0


def test_post_subtitle_image_invalid_size_returns_422():
    with TestClient(create_app()) as client:
        response = client.post(
            "/subtitle-images",
            json={"text": "hello", "width": 0, "height": 180},
        )
    assert response.status_code == 422
