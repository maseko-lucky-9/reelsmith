"""Unit tests for animated_caption_service (W2.1)."""
from __future__ import annotations

import pytest

from app.services.animated_caption_service import (
    ANIMATION_KINDS,
    CaptionWord,
    PRESETS,
    get_preset,
    plan_caption_frames,
)


def test_all_six_presets_present():
    expected = {"static", "hormozi", "mrbeast", "karaoke", "boldpop", "subtle"}
    assert set(PRESETS.keys()) == expected
    assert set(ANIMATION_KINDS) == expected


def test_preset_colours():
    assert get_preset("hormozi").highlight_color == "#fff200"
    assert get_preset("mrbeast").highlight_color == "#ff0066"
    assert get_preset("subtle").highlight_color is None


def test_get_preset_unknown_raises():
    with pytest.raises(ValueError):
        get_preset("comicsans")


def _words():
    return [
        CaptionWord("hello", 0.0, 0.5),
        CaptionWord("world", 0.5, 1.0),
        CaptionWord("now",   1.0, 1.5),
    ]


def test_plan_emits_one_frame_per_interval():
    frames = plan_caption_frames(_words(), style="hormozi", fps=10)
    # 1.5s window @ 10fps -> 15 frames
    assert len(frames) == 15
    assert frames[0].style.name == "hormozi"


def test_plan_highlights_active_word():
    frames = plan_caption_frames(_words(), style="hormozi", fps=10)
    # First frame at t=0 -> word 0 active.
    assert frames[0].highlighted_word_index == 0
    # Frame at t=0.7 -> 'world' active (index 1).
    f7 = next(f for f in frames if abs(f.timestamp - 0.7) < 1e-3)
    assert f7.highlighted_word_index == 1


def test_plan_text_is_rolling_window():
    many = [CaptionWord(f"w{i}", i * 0.2, i * 0.2 + 0.2) for i in range(10)]
    frames = plan_caption_frames(many, style="static", fps=5)
    last = frames[-1]
    assert len(last.text.split()) <= 7


def test_plan_empty_words_returns_empty():
    assert plan_caption_frames([], style="static") == []


def test_plan_zero_window_returns_empty():
    same = [CaptionWord("x", 1.0, 1.0)]  # end == start
    assert plan_caption_frames(same, style="static") == []
