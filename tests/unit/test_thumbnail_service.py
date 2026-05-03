from unittest.mock import patch

from app.services.thumbnail_service import generate_thumbnail


def test_generate_thumbnail_uses_cv2_first(tmp_path):
    output = str(tmp_path / "thumb.jpg")
    with patch("app.services.thumbnail_service._via_cv2", return_value=output) as mock_cv2:
        result = generate_thumbnail("/tmp/clip.mp4", output)
    mock_cv2.assert_called_once_with("/tmp/clip.mp4", output)
    assert result == output


def test_generate_thumbnail_falls_back_on_import_error(tmp_path):
    output = str(tmp_path / "thumb.jpg")
    with (
        patch("app.services.thumbnail_service._via_cv2", side_effect=ImportError),
        patch("app.services.thumbnail_service._via_moviepy", return_value=output) as mock_mp,
    ):
        result = generate_thumbnail("/tmp/clip.mp4", output)
    mock_mp.assert_called_once_with("/tmp/clip.mp4", output)
    assert result == output


def test_generate_thumbnail_falls_back_on_cv2_runtime_error(tmp_path):
    output = str(tmp_path / "thumb.jpg")
    with (
        patch("app.services.thumbnail_service._via_cv2", side_effect=RuntimeError("cap read failed")),
        patch("app.services.thumbnail_service._via_moviepy", return_value=output) as mock_mp,
    ):
        result = generate_thumbnail("/tmp/clip.mp4", output)
    mock_mp.assert_called_once()
    assert result == output


def test_generate_thumbnail_creates_parent_dir(tmp_path):
    nested = tmp_path / "deep" / "dir" / "thumb.jpg"
    output = str(nested)
    with (
        patch("app.services.thumbnail_service._via_cv2", return_value=output),
    ):
        generate_thumbnail("/tmp/clip.mp4", output)
    assert nested.parent.exists()
