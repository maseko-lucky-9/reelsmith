import os
from unittest.mock import MagicMock, patch

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

    video_folder, clips_folder = create_video_subfolder(
        str(tmp_path), VIDEO_URL, "youtube"
    )

    assert os.path.basename(video_folder) == "youtube_video"
    assert os.path.exists(video_folder)
    assert os.path.exists(clips_folder)


@patch("app.services.folder_service.YoutubeDL")
def test_fallback_uses_platform_id_when_provided(mock_ydl_cls, tmp_path):
    """Non-YouTube platforms get distinct fallback folder names."""
    mock_ydl_cls.side_effect = Exception("auth required")

    video_folder, _ = create_video_subfolder(
        str(tmp_path), "https://www.tiktok.com/@u/video/1", "tiktok"
    )
    assert os.path.basename(video_folder) == "tiktok_video"


@patch("app.services.folder_service.YoutubeDL")
def test_sanitises_emoji_and_special_chars(mock_ydl_cls, tmp_path):
    """TikTok/IG titles often contain emoji; slug must be filesystem-safe."""
    instance = MagicMock()
    instance.__enter__.return_value.extract_info.return_value = {
        "title": "Crazy 🚀 Reel / Part #1!"
    }
    mock_ydl_cls.return_value = instance

    video_folder, _ = create_video_subfolder(
        str(tmp_path), "https://www.tiktok.com/@u/video/1", "tiktok"
    )
    name = os.path.basename(video_folder)
    # Only word chars + dashes survive; emoji and slashes are replaced
    assert all(c.isalnum() or c in "_-" for c in name)
    assert "Crazy" in name and "Reel" in name and "Part" in name


@patch("app.services.folder_service.YoutubeDL")
def test_generate_scheme_does_not_invoke_yt_dlp(mock_ydl_cls, tmp_path):
    """generate:// URLs must skip yt-dlp entirely (no ~10s extract_info stall)."""
    video_folder, clips_folder = create_video_subfolder(
        str(tmp_path), "generate://x", "generate"
    )

    mock_ydl_cls.assert_not_called()
    assert os.path.basename(video_folder) == "generate_video"
    assert os.path.exists(video_folder)
    assert os.path.exists(clips_folder)


@patch("app.services.folder_service.YoutubeDL")
def test_upload_scheme_does_not_invoke_yt_dlp(mock_ydl_cls, tmp_path):
    """upload:// URLs share the same latent stall fix as generate://."""
    video_folder, _ = create_video_subfolder(
        str(tmp_path), "upload:///tmp/yt/uploads/x.mp4", "upload"
    )

    mock_ydl_cls.assert_not_called()
    assert os.path.basename(video_folder) == "upload_video"


@pytest.mark.integration
def test_against_live_youtube(tmp_path):
    """Hits real YouTube; opt-in via -m integration."""
    video_folder, clips_folder = create_video_subfolder(str(tmp_path), VIDEO_URL)
    assert os.path.exists(video_folder)
    assert os.path.exists(clips_folder)


@pytest.mark.parametrize(
    ("platform_id", "group"),
    [
        ("youtube", "YouTube Videos"),
        ("tiktok", "TikTok Videos"),
        ("facebook", "Facebook Videos"),
        ("instagram", "Instagram Videos"),
    ],
)
@patch("app.services.folder_service.YoutubeDL")
def test_job_folder_is_grouped_under_its_source_folder(mock_ydl_cls, tmp_path, platform_id, group):
    mock_ydl_cls.side_effect = Exception("offline")

    video_folder, clips_folder = create_video_subfolder(
        str(tmp_path), "https://x.test/v", platform_id, job_id="abcdef1234567890"
    )

    assert video_folder == str(tmp_path / group / f"{platform_id}_video-abcdef12")
    assert clips_folder == os.path.join(video_folder, "clips")
    assert os.path.isdir(clips_folder)


@pytest.mark.parametrize("platform_id", ["upload", "generate", "video", "unknown"])
def test_sources_without_a_group_stay_directly_under_the_download_path(tmp_path, platform_id):
    video_folder, _ = create_video_subfolder(
        str(tmp_path), "upload://x.mp4", platform_id, job_id="abcdef1234567890"
    )

    assert os.path.dirname(video_folder) == str(tmp_path)
