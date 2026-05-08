import numpy as np

from app.services.subtitle_image_service import create_subtitle_image, render_to_path

_SIZE = (640, 360)


def test_returns_rgba_array_with_expected_shape():
    arr = create_subtitle_image("hello", _SIZE, font_size=20)
    assert isinstance(arr, np.ndarray)
    assert arr.shape == (360, 640, 4)


def test_no_full_width_opaque_bar_at_bottom():
    """Regression: legacy full-width black bar replaced with per-word pill."""
    arr = create_subtitle_image("hello", (320, 180), font_size=20)
    # No bottom row should be 100% opaque across its full width.
    bottom_30 = arr[-30:, :, 3]
    fully_opaque_rows = (bottom_30 == 255).all(axis=1).sum()
    assert fully_opaque_rows == 0


def test_active_word_renders_green():
    """The active word's glyph fill should be green (no pill)."""
    arr = create_subtitle_image(
        "ALPHA BETA GAMMA", (1080, 1920),
        font_size=96, highlight_word_index=1, text_anchor_y=1500,
    )
    # Sample the row band where the text sits.
    band = arr[1450:1550, :, :]
    r, g, b, a = band[..., 0], band[..., 1], band[..., 2], band[..., 3]
    green_mask = (g > 150) & (r < 120) & (b < 120) & (a > 200)
    assert green_mask.sum() > 50, "expected green glyph pixels for the active word"


def test_drop_shadow_present_below_text():
    """A blurred shadow should add semi-transparent pixels offset below the glyphs."""
    arr = create_subtitle_image(
        "HELLO", (640, 720), font_size=80, text_anchor_y=300,
    )
    # Shadow lives a few rows below the glyph baseline; sample a strip there.
    strip = arr[340:360, :, :]
    semi = (strip[..., 3] > 30) & (strip[..., 3] < 220)
    assert semi.sum() > 20, "expected partially-transparent shadow pixels below text"


def test_text_anchor_y_centers_text():
    arr = create_subtitle_image(
        "HELLO", (640, 720), font_size=60, text_anchor_y=300,
    )
    # Locate topmost non-transparent row.
    nonzero_rows = (arr[..., 3] > 0).any(axis=1)
    top = int(nonzero_rows.argmax())
    # Text should be centred near y=300 — top edge above 300, bottom below.
    assert 200 <= top <= 300


def test_default_font_resolves():
    """Bundled Anton (or system fallback) must resolve to a real file."""
    from app.settings import _default_font_path
    path = _default_font_path()
    assert path is not None, "no usable default font on this system"


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
