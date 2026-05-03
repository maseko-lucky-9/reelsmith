from unittest.mock import MagicMock, patch

import pytest

from app.services.render_service import _load_captions, render_clip


def test_load_captions_srt():
    with patch("app.services.render_service.pysrt") as mock_pysrt:
        mock_pysrt.open.return_value = ["sub1", "sub2"]
        result = _load_captions("/tmp/subs.srt")
    mock_pysrt.open.assert_called_once_with("/tmp/subs.srt")
    assert result == ["sub1", "sub2"]


def test_load_captions_vtt():
    with patch("app.services.render_service.WebVTT") as mock_webvtt:
        mock_instance = MagicMock()
        mock_webvtt.return_value = mock_instance
        mock_instance.read.return_value = iter(["vtt1", "vtt2"])
        result = _load_captions("/tmp/subs.vtt")
    mock_instance.read.assert_called_once_with("/tmp/subs.vtt")
    assert result == ["vtt1", "vtt2"]


def test_load_captions_unsupported_extension_raises():
    with pytest.raises(ValueError, match="Unsupported captions extension"):
        _load_captions("/tmp/subs.txt")


def _make_mock_cm(mock_video):
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_video)
    mock_cm.__exit__ = MagicMock(return_value=False)
    return mock_cm


def test_render_clip_no_captions(tmp_path):
    output = str(tmp_path / "out.mp4")
    mock_video = MagicMock()
    mock_sub = MagicMock()
    mock_video.subclip.return_value = mock_sub

    with patch("app.services.render_service.closing_clip", return_value=_make_mock_cm(mock_video)):
        render_clip("/tmp/v.mp4", output, 0.0, 5.0)

    mock_video.subclip.assert_called_once_with(0.0, 5.0)
    mock_sub.write_videofile.assert_called_once()


def test_render_clip_with_captions_path(tmp_path):
    output = str(tmp_path / "out.mp4")
    mock_video = MagicMock()
    mock_sub = MagicMock()
    mock_final = MagicMock()
    mock_video.subclip.return_value = mock_sub

    with (
        patch("app.services.render_service.closing_clip", return_value=_make_mock_cm(mock_video)),
        patch("app.services.render_service._load_captions", return_value=["cap1"]),
        patch("app.services.render_service.add_captions_to_clip", return_value=mock_final),
    ):
        render_clip("/tmp/v.mp4", output, 0.0, 5.0, captions_path="/tmp/subs.srt")

    mock_final.write_videofile.assert_called_once()


def test_render_clip_with_word_timings(tmp_path):
    output = str(tmp_path / "out.mp4")
    word_timings = [{"word": "hello", "start": 0.0, "end": 0.5}]
    mock_video = MagicMock()
    mock_sub = MagicMock()
    mock_final = MagicMock()
    mock_video.subclip.return_value = mock_sub

    with (
        patch("app.services.render_service.closing_clip", return_value=_make_mock_cm(mock_video)),
        patch("app.services.render_service.add_captions_to_clip", return_value=mock_final),
    ):
        render_clip("/tmp/v.mp4", output, 0.0, 5.0, word_timings=word_timings)

    mock_final.write_videofile.assert_called_once()


def test_render_clip_returns_output_path(tmp_path):
    output = str(tmp_path / "out.mp4")
    mock_video = MagicMock()
    mock_sub = MagicMock()
    mock_video.subclip.return_value = mock_sub

    with patch("app.services.render_service.closing_clip", return_value=_make_mock_cm(mock_video)):
        result = render_clip("/tmp/v.mp4", output, 0.0, 5.0)

    assert result == output
