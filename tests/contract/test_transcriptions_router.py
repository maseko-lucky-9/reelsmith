from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app


def test_post_transcription_missing_file_returns_404():
    with TestClient(create_app()) as client:
        response = client.post(
            "/transcriptions",
            json={"audio_path": "/nope/missing.wav"},
        )
    assert response.status_code == 404


@patch("app.routers.transcriptions.speech_to_text", return_value="hello world")
def test_post_transcription_returns_text(mock_stt, tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00")
    with TestClient(create_app()) as client:
        response = client.post("/transcriptions", json={"audio_path": str(audio)})
    assert response.status_code == 200
    assert response.json()["text"] == "hello world"
    mock_stt.assert_called_once()
