"""Unit tests for W2.5 + W2.6 + W2.9."""
from __future__ import annotations

import pytest

from app.services import (
    filler_removal_service as filler,
    profanity_filter_service as prof,
    transition_service as trans,
)


# ── W2.5 filler_removal_service ────────────────────────────────────────────


def test_filler_removes_um(monkeypatch):
    words = [
        filler.WordSpan("Hello", 0.0, 0.5),
        filler.WordSpan("um", 0.5, 0.7),
        filler.WordSpan("world", 0.7, 1.2),
    ]
    intervals = filler.plan_keep_intervals(words, max_silence_seconds=2.0,
                                           pad_seconds=0.0)
    # The 'um' is dropped → kept span is 0.0..0.5 + 0.7..1.2.
    assert len(intervals) == 1  # coalesced because gap (0.5→0.7 = 0.2) <= max_silence
    assert intervals[0] == (0.0, 1.2)


def test_filler_splits_on_long_silence():
    words = [
        filler.WordSpan("hi", 0.0, 0.3),
        filler.WordSpan("there", 5.0, 5.5),  # long silence > max
    ]
    intervals = filler.plan_keep_intervals(words, max_silence_seconds=0.6,
                                           pad_seconds=0.0)
    assert len(intervals) == 2


def test_filler_total_kept_shrinks_when_filler_present():
    words_clean = [
        filler.WordSpan("hello", 0.0, 0.5),
        filler.WordSpan("world", 0.5, 1.0),
        filler.WordSpan("now", 1.0, 1.5),
    ]
    words_with_filler = words_clean[:1] + [
        filler.WordSpan("um", 0.5, 0.7),
        filler.WordSpan("world", 0.7, 1.2),
        filler.WordSpan("uh", 1.2, 1.4),
        filler.WordSpan("now", 1.4, 1.9),
    ]
    a = filler.total_kept(filler.plan_keep_intervals(words_clean, pad_seconds=0))
    b = filler.total_kept(filler.plan_keep_intervals(words_with_filler, pad_seconds=0))
    # Cleanup of fillers must shrink the kept duration.
    assert b < a + 0.401  # 'with filler' loses 'um' (0.2s) + 'uh' (0.2s) = 0.4s


def test_filler_empty_returns_empty():
    assert filler.plan_keep_intervals([]) == []


def test_filler_custom_word_list():
    words = [
        filler.WordSpan("kinda", 0.0, 0.3),
        filler.WordSpan("hello", 0.3, 0.7),
    ]
    intervals = filler.plan_keep_intervals(
        words, filler_words=("kinda",), pad_seconds=0
    )
    assert intervals == [(0.3, 0.7)]


# ── W2.6 transition_service ────────────────────────────────────────────────


def test_xfade_filter_fade():
    f = trans.xfade_filter("fade", duration=0.5, offset=4.5)
    assert "transition=fade" in f
    assert "duration=0.500" in f
    assert "offset=4.500" in f
    assert f.startswith("[0:v][1:v]xfade=")


def test_xfade_filter_slide():
    f = trans.xfade_filter("slide-left", duration=1.0, offset=0.0)
    assert "transition=slideleft" in f


def test_xfade_filter_zoom():
    f = trans.xfade_filter("zoom", duration=1.0, offset=0.0)
    assert "transition=zoomin" in f


def test_xfade_filter_unknown_raises():
    with pytest.raises(ValueError):
        trans.xfade_filter("vortex", duration=1.0, offset=0.0)  # type: ignore[arg-type]


def test_xfade_filter_zero_duration_raises():
    with pytest.raises(ValueError):
        trans.xfade_filter("fade", duration=0.0, offset=0.0)


# ── W2.9 profanity_filter_service ──────────────────────────────────────────


def test_profanity_default_bleeps():
    out = prof.filter_text("This is fucking ridiculous", mode="default")
    assert "f******" in out.lower()
    assert "fucking" not in out.lower()


def test_profanity_off_passthrough():
    s = "fucking heck"
    assert prof.filter_text(s, mode="off") == s


def test_profanity_custom_list():
    out = prof.filter_text(
        "monday is the worst", mode="custom", custom_words=("monday",)
    )
    assert "m*****" in out
    assert "monday" not in out.lower()


def test_profanity_unknown_mode_raises():
    with pytest.raises(ValueError):
        prof.filter_text("x", mode="bogus")


def test_profanity_word_boundary():
    # 'shit' should not match in 'shittake'.
    out = prof.filter_text("shittake mushroom", mode="default")
    assert "shittake" in out  # untouched
