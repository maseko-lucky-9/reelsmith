from pathlib import Path

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


def test_upload_reads_body_in_1_mib_chunks(monkeypatch, tmp_path):
    from starlette.datastructures import UploadFile

    monkeypatch.setattr("app.routers.uploads._UPLOAD_DIR", tmp_path / "uploads")
    sizes: list[int] = []
    real_read = UploadFile.read

    async def spy(self, size: int = -1):
        sizes.append(size)
        return await real_read(self, size)

    monkeypatch.setattr(UploadFile, "read", spy)
    payload = b"\x01" * (2 * 1024 * 1024 + 7)
    with TestClient(create_app()) as client:
        response = client.post(
            "/uploads",
            files={"file": ("video.mp4", payload, "video/mp4")},
        )
    assert response.status_code == 202
    assert set(sizes) == {1024 * 1024}
    assert len(sizes) == 4  # 1 MiB, 1 MiB, 7 bytes, EOF
    assert Path(response.json()["upload_path"]).read_bytes() == payload
