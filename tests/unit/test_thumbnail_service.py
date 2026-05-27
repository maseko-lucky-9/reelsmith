import hashlib
import sys
from unittest.mock import MagicMock, patch

import pytest

from app.services.thumbnail_service import ThumbnailError, compose_thumbnail, generate_thumbnail


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


# ---------------------------------------------------------------------------
# compose_thumbnail tests
# ---------------------------------------------------------------------------

def _make_fake_jpeg(path, width=320, height=569):
    """Write a real minimal JPEG to *path* using Pillow (if available)."""
    from PIL import Image
    img = Image.new("RGB", (width, height), color=(100, 149, 237))
    img.save(str(path), "JPEG", quality=85)


def _patch_generate(tmp_path, output_path):
    """Return a patch that writes a real JPEG instead of extracting a frame."""
    def _fake_generate(clip_path, out_path):
        _make_fake_jpeg(out_path)
        return out_path

    return patch("app.services.thumbnail_service.generate_thumbnail", side_effect=_fake_generate)


def test_compose_thumbnail_returns_output_path(tmp_path):
    output = str(tmp_path / "composed.jpg")
    with _patch_generate(tmp_path, output):
        result = compose_thumbnail("/tmp/clip.mp4", output, headline="Hello World")
    assert result == output


def test_compose_thumbnail_produces_correct_dimensions(tmp_path):
    from PIL import Image

    output = str(tmp_path / "composed.jpg")
    with _patch_generate(tmp_path, output):
        compose_thumbnail("/tmp/clip.mp4", output, headline="Test Headline")

    img = Image.open(output)
    assert img.size == (320, 569), f"Expected 320x569, got {img.size}"


def test_compose_thumbnail_empty_headline_byte_identical_to_generate_thumbnail(tmp_path):
    """headline='' must produce a file byte-identical to generate_thumbnail.

    Both generate_thumbnail and compose_thumbnail are patched to write the same
    fixed JPEG fixture, so the assertion is that compose_thumbnail with an empty
    headline does NOT re-write the file (i.e., it returns after generate_thumbnail
    without any further processing), producing the same bytes.
    """
    raw_output = str(tmp_path / "raw.jpg")
    composed_output = str(tmp_path / "composed.jpg")

    # Write a fixed reference JPEG for generate_thumbnail to "produce"
    _make_fake_jpeg(raw_output)
    _make_fake_jpeg(composed_output)

    raw_hash = hashlib.sha256(open(raw_output, "rb").read()).hexdigest()

    def _fake_generate(clip_path, out_path):
        # Simulate generate_thumbnail writing the same fixed image content
        _make_fake_jpeg(out_path)
        return out_path

    # compose_thumbnail with empty headline should produce same bytes as generate_thumbnail alone
    with patch("app.services.thumbnail_service.generate_thumbnail", side_effect=_fake_generate):
        compose_thumbnail("/tmp/clip.mp4", composed_output, headline="")

    composed_hash = hashlib.sha256(open(composed_output, "rb").read()).hexdigest()
    assert raw_hash == composed_hash, "Empty headline should produce byte-identical output to generate_thumbnail"


def test_compose_thumbnail_pillow_unavailable_raises_thumbnail_error(tmp_path):
    """When Pillow is not importable and headline is non-empty, ThumbnailError is raised."""
    # Use a separate fixture file so the fake generate_thumbnail can copy it
    fixture = tmp_path / "fixture.jpg"
    output = str(tmp_path / "composed.jpg")

    # Pre-write the fixture JPEG (before nulling PIL)
    _make_fake_jpeg(str(fixture))
    fixture_bytes = fixture.read_bytes()

    def _fake_generate_no_pil(clip_path, out_path):
        # Write raw bytes — no PIL import required
        import pathlib
        pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(out_path).write_bytes(fixture_bytes)
        return out_path

    # Temporarily hide PIL from sys.modules to simulate it being absent
    pil_modules = {k: v for k, v in sys.modules.items() if k == "PIL" or k.startswith("PIL.")}
    for key in pil_modules:
        sys.modules[key] = None  # type: ignore[assignment]
    try:
        with patch("app.services.thumbnail_service.generate_thumbnail", side_effect=_fake_generate_no_pil):
            with pytest.raises(ThumbnailError, match="Pillow"):
                compose_thumbnail("/tmp/clip.mp4", output, headline="Blocked")
    finally:
        # Restore PIL modules
        for key in pil_modules:
            sys.modules[key] = pil_modules[key]


def test_compose_thumbnail_position_top(tmp_path):
    """position='top' should still produce a valid 320x569 JPEG."""
    from PIL import Image

    output = str(tmp_path / "top.jpg")
    with _patch_generate(tmp_path, output):
        compose_thumbnail("/tmp/clip.mp4", output, headline="Top Text", position="top")

    img = Image.open(output)
    assert img.size == (320, 569)


def test_compose_thumbnail_custom_font_path_fallback(tmp_path):
    """An invalid font_path silently falls back to default font (no crash)."""
    from PIL import Image

    output = str(tmp_path / "font_fallback.jpg")
    with _patch_generate(tmp_path, output):
        # Non-existent font path — should fall back gracefully
        compose_thumbnail(
            "/tmp/clip.mp4", output,
            headline="Fallback Font",
            font_path="/nonexistent/font.ttf",
        )

    img = Image.open(output)
    assert img.size == (320, 569)
