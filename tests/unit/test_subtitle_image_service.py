import numpy as np

from app.services.subtitle_image_service import create_subtitle_image, render_to_path


def test_returns_rgba_array_with_expected_shape():
    arr = create_subtitle_image("hello", (640, 360), font_size=20)
    assert isinstance(arr, np.ndarray)
    assert arr.shape == (360, 640, 4)


def test_bottom_strip_is_opaque_dark_background():
    arr = create_subtitle_image("hello", (320, 180), font_size=20)
    bottom_strip = arr[-5:, :, :]
    # Alpha channel fully opaque on bottom strip
    assert int(bottom_strip[..., 3].mean()) == 255


def test_render_to_path_writes_png(tmp_path):
    out = tmp_path / "subtitle.png"
    render_to_path("hi", (320, 180), str(out), font_size=20)
    assert out.is_file()
    assert out.stat().st_size > 0
