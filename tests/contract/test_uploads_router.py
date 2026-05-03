from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import settings as _settings


def test_upload_wrong_mime_type_returns_415():
    with TestClient(create_app()) as client:
        response = client.post(
            "/uploads",
            files={"file": ("video.txt", b"data", "text/plain")},
        )
    assert response.status_code == 415


def test_upload_oversized_file_returns_413(monkeypatch, tmp_path):
    monkeypatch.setattr(_settings, "max_upload_mb", 0)
    monkeypatch.setattr("app.routers.uploads._UPLOAD_DIR", tmp_path / "uploads")
    with TestClient(create_app()) as client:
        response = client.post(
            "/uploads",
            files={"file": ("video.mp4", b"\x00" * 100, "video/mp4")},
        )
    assert response.status_code == 413


def test_upload_valid_video_returns_202(monkeypatch, tmp_path):
    monkeypatch.setattr("app.routers.uploads._UPLOAD_DIR", tmp_path / "uploads")
    with TestClient(create_app()) as client:
        response = client.post(
            "/uploads",
            files={"file": ("video.mp4", b"\x00" * 64, "video/mp4")},
        )
    assert response.status_code == 202
    body = response.json()
    assert "job_id" in body
    assert body["status"] == "queued"


def test_upload_quicktime_mime_accepted(monkeypatch, tmp_path):
    monkeypatch.setattr("app.routers.uploads._UPLOAD_DIR", tmp_path / "uploads")
    with TestClient(create_app()) as client:
        response = client.post(
            "/uploads",
            files={"file": ("video.mov", b"\x00" * 64, "video/quicktime")},
        )
    assert response.status_code == 202
