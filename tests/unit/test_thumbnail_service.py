import hashlib
import sys
from unittest.mock import patch

import pytest

from app.services.thumbnail_service import ThumbnailError, compose_thumbnail, generate_thumbnail


def test_generate_thumbnail_real_midpoint_frame(sync_fixture_640, tmp_path):
    """Decodes the midpoint frame with PyAV and writes a 320x569 (9:16) JPEG."""
    from PIL import Image

    output = str(tmp_path / "thumb.jpg")
    assert generate_thumbnail(str(sync_fixture_640.path), output) == output
    img = Image.open(output)
    assert img.format == "JPEG"
    assert img.size == (320, 569)


def test_generate_thumbnail_grabs_midpoint_and_center_crops(tmp_path):
    """Landscape frame → centre column of 9:16 width, resized to 320x569."""
    from PIL import Image

    frame = Image.new("RGB", (1600, 900), (0, 0, 255))
    # 9:16 crop of a 900-tall frame is int(900 * 320/569) = 506 px wide, centred.
    frame.paste((255, 0, 0), (547, 0, 1053, 900))
    output = str(tmp_path / "deep" / "dir" / "thumb.jpg")
    with (
        patch("app.services.thumbnail_service.ffmpeg_tools.duration", return_value=8.0),
        patch(
            "app.services.thumbnail_service.ffmpeg_tools.grab_frame", return_value=frame
        ) as grab,
    ):
        result = generate_thumbnail("/tmp/clip.mp4", output)
    grab.assert_called_once_with("/tmp/clip.mp4", 4.0)
    assert result == output
    img = Image.open(output).convert("RGB")
    assert img.size == (320, 569)
    r, g, b = img.resize((1, 1)).getpixel((0, 0))
    assert r > 200 and b < 60  # only the red centre column survived the crop


def test_generate_thumbnail_portrait_crops_vertically(tmp_path):
    from PIL import Image

    frame = Image.new("RGB", (720, 2000), (0, 0, 255))
    # 9:16 crop of a 720-wide frame is int(720 / (320/569)) = 1280 px tall.
    frame.paste((0, 255, 0), (0, 360, 720, 1640))
    output = str(tmp_path / "thumb.jpg")
    with (
        patch("app.services.thumbnail_service.ffmpeg_tools.duration", return_value=2.0),
        patch("app.services.thumbnail_service.ffmpeg_tools.grab_frame", return_value=frame),
    ):
        generate_thumbnail("/tmp/clip.mp4", output)
    r, g, b = Image.open(output).convert("RGB").resize((1, 1)).getpixel((0, 0))
    assert g > 200 and b < 60


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
