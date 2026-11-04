"""Text bounds on a timeline (spec 016 Phase 4a, security M1/M2): caption
tokens per caption and per timeline, overlay line breaks and overlay
characters per timeline are capped, so a bad timeline fails at PUT time
(``validate_timeline``) rather than at render time; and a text overlay image
is never larger than the reel's canvas."""

from __future__ import annotations

import pytest

from app.services import subtitle_image_service
from app.services.timeline_render_service import (
    MAX_CAPTION_WORDS,
    MAX_OVERLAY_LINES,
    MAX_TIMELINE_CAPTION_WORDS,
    MAX_TIMELINE_OVERLAY_CHARS,
    TimelineError,
    validate_timeline,
)
from app.settings import _REPO_ANTON, settings


def _v2(captions=(), overlays=()):
    return {
        "schema_version": 2,
        "tracks": [
            {"kind": "video", "items": [{"start": 0.0, "end": 600.0}]},
            {"kind": "caption", "items": list(captions)},
            {"kind": "text-overlay", "items": list(overlays)},
        ],
    }


def _caption(i, words):
    return {"start": float(i) * 0.25, "end": float(i) * 0.25 + 0.25, "text": " ".join(["w"] * words)}


def test_a_caption_with_too_many_text_tokens_is_refused():
    with pytest.raises(TimelineError, match=f"more than {MAX_CAPTION_WORDS} words"):
        validate_timeline(_v2(captions=[_caption(0, MAX_CAPTION_WORDS + 1)]))
    validate_timeline(_v2(captions=[_caption(0, MAX_CAPTION_WORDS)]))


def test_too_many_caption_words_across_the_timeline_are_refused():
    per = MAX_CAPTION_WORDS
    n = MAX_TIMELINE_CAPTION_WORDS // per
    validate_timeline(_v2(captions=[_caption(i, per) for i in range(n)]))
    with pytest.raises(TimelineError, match=f"more than {MAX_TIMELINE_CAPTION_WORDS} words in all"):
        validate_timeline(_v2(captions=[_caption(i, per) for i in range(n)] + [_caption(n, 1)]))


def _overlay(i, text):
    return {"start": float(i), "end": float(i) + 1.0, "text": text, "x": 0.5, "y": 0.5}


def test_an_overlay_with_too_many_line_breaks_is_refused():
    validate_timeline(_v2(overlays=[_overlay(0, "\n".join(["a"] * (MAX_OVERLAY_LINES + 1)))]))
    with pytest.raises(TimelineError, match="line breaks"):
        validate_timeline(_v2(overlays=[_overlay(0, "\n".join(["a"] * (MAX_OVERLAY_LINES + 2)))]))


def test_too_many_overlay_characters_across_the_timeline_are_refused():
    n = MAX_TIMELINE_OVERLAY_CHARS // 500
    validate_timeline(_v2(overlays=[_overlay(i, "x" * 500) for i in range(n)]))
    with pytest.raises(TimelineError, match=f"more than {MAX_TIMELINE_OVERLAY_CHARS} characters"):
        validate_timeline(_v2(overlays=[_overlay(i, "x" * 500) for i in range(n)] + [_overlay(n, "x")]))


@pytest.fixture
def anton(monkeypatch):
    assert _REPO_ANTON.is_file()  # the repo ships the font
    monkeypatch.setattr(settings, "font_path", str(_REPO_ANTON))


@pytest.mark.parametrize("text", ["W" * 500, " ".join(["WIDE"] * 120), "W\n" * 10 + "W" * 400])
def test_a_huge_overlay_never_exceeds_the_canvas(anton, text):
    canvas = (640, 1137)
    img = subtitle_image_service.create_text_overlay_image(text, 400, "#ffffff", canvas)
    assert img is not None
    assert img.width <= canvas[0] and img.height <= canvas[1]


def test_an_overlay_too_big_even_at_the_smallest_font_is_scaled_into_a_tiny_canvas(anton):
    canvas = (24, 24)
    img = subtitle_image_service.create_text_overlay_image("W" * 500, 400, None, canvas)
    assert img is not None
    assert img.width <= canvas[0] and img.height <= canvas[1]


def test_overlay_text_is_wrapped_to_the_canvas_width(anton):
    canvas = (640, 1137)
    unwrapped = subtitle_image_service.create_text_overlay_image("HELLO " * 12, 60, None)
    wrapped = subtitle_image_service.create_text_overlay_image("HELLO " * 12, 60, None, canvas)
    assert unwrapped.width > canvas[0]
    assert wrapped.width <= canvas[0]
    # Wrapped, not scaled down: taller than one line at the same font size.
    assert wrapped.height > 2 * unwrapped.height
