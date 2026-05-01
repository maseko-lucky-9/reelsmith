import pytest
from unittest.mock import MagicMock

from app.services.clip_service import create_clip


def test_valid_clip_creation():
    video = MagicMock()
    video.subclip.return_value = "clip"
    assert create_clip(video, 10, 20) == "clip"


def test_invalid_start_time():
    video = MagicMock()
    with pytest.raises(ValueError):
        create_clip(video, -10, 20)


def test_invalid_end_time():
    video = MagicMock()
    with pytest.raises(ValueError):
        create_clip(video, 10, -20)


def test_start_time_greater_than_end_time():
    video = MagicMock()
    with pytest.raises(ValueError):
        create_clip(video, 20, 10)


def test_start_time_equal_to_end_time():
    video = MagicMock()
    with pytest.raises(ValueError):
        create_clip(video, 10, 10)


def test_video_object_without_subclip_method():
    video = object()
    with pytest.raises(AttributeError):
        create_clip(video, 10, 20)
