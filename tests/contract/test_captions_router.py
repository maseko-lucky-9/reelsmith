from fastapi.testclient import TestClient

from app.main import create_app


def _client():
    return TestClient(create_app())


def test_post_captions_srt_happy_path():
    with _client() as client:
        response = client.post(
            "/captions",
            json={"text": "hello world how are you doing today", "start": 0, "end": 5, "format": "srt"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["format"] == "srt"
    assert data["body"]
    assert data["parsed"]
    assert {"index", "start", "end", "text"} <= set(data["parsed"][0])


def test_post_captions_invalid_range_returns_422():
    with _client() as client:
        response = client.post(
            "/captions",
            json={"text": "hello", "start": 5, "end": 1, "format": "srt"},
        )
    assert response.status_code == 422


def test_post_captions_invalid_format_returns_422():
    with _client() as client:
        response = client.post(
            "/captions",
            json={"text": "hello", "start": 0, "end": 5, "format": "txt"},
        )
    assert response.status_code == 422
