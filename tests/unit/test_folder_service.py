import os
from unittest.mock import patch, MagicMock

import pytest

from app.services.folder_service import create_video_subfolder


VIDEO_URL = "https://www.youtube.com/watch?v=y5dCjpptTVU"


@patch("app.services.folder_service.YoutubeDL")
def test_creates_named_folders_when_metadata_succeeds(mock_ydl_cls, tmp_path):
    instance = MagicMock()
    instance.__enter__.return_value.extract_info.return_value = {"title": "My Video"}
    mock_ydl_cls.return_value = instance

    video_folder, clips_folder = create_video_subfolder(str(tmp_path), VIDEO_URL)

    assert os.path.basename(video_folder) == "My_Video"
    assert os.path.exists(video_folder)
    assert os.path.exists(clips_folder)
    assert clips_folder == os.path.join(video_folder, "clips")


@patch("app.services.folder_service.YoutubeDL")
def test_falls_back_to_generic_folder_when_metadata_fails(mock_ydl_cls, tmp_path):
    mock_ydl_cls.side_effect = Exception("network down")

    video_folder, clips_folder = create_video_subfolder(str(tmp_path), VIDEO_URL)

    assert os.path.basename(video_folder) == "youtube_video"
    assert os.path.exists(video_folder)
    assert os.path.exists(clips_folder)


@pytest.mark.integration
def test_against_live_youtube(tmp_path):
    """Hits real YouTube; opt-in via -m integration."""
    video_folder, clips_folder = create_video_subfolder(str(tmp_path), VIDEO_URL)
    assert os.path.exists(video_folder)
    assert os.path.exists(clips_folder)
