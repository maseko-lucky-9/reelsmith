import numpy as np

from app.services.subtitle_image_service import create_subtitle_image, render_to_path

_SIZE = (640, 360)


def test_returns_rgba_array_with_expected_shape():
    arr = create_subtitle_image("hello", _SIZE, font_size=20)
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


# ── Karaoke / highlight tests ────────────────────────────────────────────────

def test_highlight_word_index_returns_correct_shape():
    arr = create_subtitle_image("hello world foo", _SIZE, font_size=20, highlight_word_index=1)
    assert arr.shape == (360, 640, 4)


def test_no_highlight_returns_correct_shape():
    arr = create_subtitle_image("hello world", _SIZE, font_size=20, highlight_word_index=None)
    assert arr.shape == (360, 640, 4)


def test_highlight_first_word_no_error():
    arr = create_subtitle_image("hello world foo", _SIZE, font_size=20, highlight_word_index=0)
    assert arr.shape == (360, 640, 4)


def test_highlight_last_word_no_error():
    arr = create_subtitle_image("hello world foo", _SIZE, font_size=20, highlight_word_index=2)
    assert arr.shape == (360, 640, 4)


def test_single_word_highlight_no_error():
    arr = create_subtitle_image("only", _SIZE, font_size=20, highlight_word_index=0)
    assert arr.shape == (360, 640, 4)


def test_highlight_produces_different_image_than_no_highlight():
    plain = create_subtitle_image("hello world foo", _SIZE, font_size=20)
    highlighted = create_subtitle_image("hello world foo", _SIZE, font_size=20, highlight_word_index=0)
    assert not np.array_equal(plain, highlighted)


def test_lowercase_input_accepted():
    arr = create_subtitle_image("hello world", _SIZE, font_size=20)
    assert arr.shape == (360, 640, 4)


def test_render_to_path_with_highlight_creates_file(tmp_path):
    out = tmp_path / "sub_highlight.png"
    render_to_path("hello world foo", _SIZE, str(out), font_size=20, highlight_word_index=1)
    assert out.is_file()
    assert out.stat().st_size > 0
