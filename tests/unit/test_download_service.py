from unittest.mock import MagicMock, patch

from app.services.download_service import (
    extract_chapters,
    is_supported_url,
    download_video,
)


def test_is_supported_url_youtube():
    assert is_supported_url("https://www.youtube.com/watch?v=abc") is True


def test_is_supported_url_youtu_be():
    assert is_supported_url("https://youtu.be/abc123") is True


def test_is_supported_url_tiktok():
    assert is_supported_url("https://www.tiktok.com/@user/video/123") is True


def test_is_supported_url_vimeo():
    assert is_supported_url("https://vimeo.com/12345") is True


def test_is_supported_url_unsupported_domain():
    assert is_supported_url("https://example.com/video") is False


def test_is_supported_url_upload_scheme():
    assert is_supported_url("upload:///tmp/uploaded.mp4") is True


def test_is_supported_url_malformed_returns_false():
    assert is_supported_url("not-a-url") is False


def test_extract_chapters_empty_info():
    assert extract_chapters({}) == []


def test_extract_chapters_none_chapters():
    assert extract_chapters({"chapters": None}) == []


def test_extract_chapters_parses_correctly():
    info = {
        "chapters": [
            {"title": "Intro", "start_time": 0.0, "end_time": 30.0},
            {"title": "Main", "start_time": 30.0, "end_time": 120.0},
        ]
    }
    result = extract_chapters(info)
    assert len(result) == 2
    assert result[0] == {"index": 0, "title": "Intro", "start": 0.0, "end": 30.0}
    assert result[1] == {"index": 1, "title": "Main", "start": 30.0, "end": 120.0}


def _mock_ydl(info, filename):
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.return_value = info
    mock_ydl.prepare_filename.return_value = filename
    return mock_ydl


def test_download_video_returns_filename_on_success(tmp_path):
    info = {"title": "Test Video", "duration": 120}
    expected = str(tmp_path / "Test Video.mp4")

    with patch("app.services.download_service.YoutubeDL", return_value=_mock_ydl(info, expected)):
        filename, returned_info = download_video("https://youtu.be/abc", str(tmp_path))

    assert filename == expected
    assert returned_info == info


def test_download_video_returns_none_on_failure(tmp_path):
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.side_effect = Exception("network error")

    with patch("app.services.download_service.YoutubeDL", return_value=mock_ydl):
        filename, info = download_video("https://youtu.be/abc", str(tmp_path))

    assert filename is None
    assert info is None
