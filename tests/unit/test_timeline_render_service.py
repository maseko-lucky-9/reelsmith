"""Unit tests for timeline_render_service.build_render_plan (W1.12)."""
from __future__ import annotations

import pytest

from app.services.timeline_render_service import (
    TimelineError,
    build_render_plan,
)


_BASE = "/render/main.mp4"


def _tl(*tracks):
    return {"tracks": list(tracks)}


def test_build_plan_minimal_video_only():
    plan = build_render_plan(
        _tl({"kind": "video", "items": [{"start": 0, "end": 10}]}),
        _BASE,
    )
    assert plan.duration == 10
    assert plan.video[0].src == _BASE
    assert plan.video[0].trim_start == 0


def test_build_plan_resolves_main_src_to_base():
    plan = build_render_plan(
        _tl({"kind": "video", "items": [{"start": 0, "end": 5, "src": "main"}]}),
        _BASE,
    )
    assert plan.video[0].src == _BASE


def test_build_plan_keeps_explicit_src():
    plan = build_render_plan(
        _tl({"kind": "video", "items": [{"start": 0, "end": 5, "src": "/custom.mp4"}]}),
        _BASE,
    )
    assert plan.video[0].src == "/custom.mp4"


def test_build_plan_all_three_tracks():
    plan = build_render_plan(
        _tl(
            {"kind": "video",        "items": [{"start": 0, "end": 12}]},
            {"kind": "caption",      "items": [{"start": 0, "end": 6, "text": "hi"}]},
            {"kind": "text-overlay", "items": [{"start": 1, "end": 4, "text": "🔥",
                                                 "x": 0.5, "y": 0.1, "font_size": 48}]},
        ),
        _BASE,
    )
    assert plan.duration == 12
    assert plan.captions[0].text == "hi"
    assert plan.overlays[0].font_size == 48
    assert plan.overlays[0].color == "#ffffff"


def test_unknown_track_kind_rejected():
    with pytest.raises(TimelineError):
        build_render_plan(
            _tl({"kind": "audio", "items": []}),
            _BASE,
        )


def test_invalid_item_end_before_start():
    with pytest.raises(TimelineError):
        build_render_plan(
            _tl({"kind": "video", "items": [{"start": 5, "end": 3}]}),
            _BASE,
        )


def test_missing_tracks_key_rejected():
    with pytest.raises(TimelineError):
        build_render_plan({}, _BASE)


def test_non_list_tracks_rejected():
    with pytest.raises(TimelineError):
        build_render_plan({"tracks": "nope"}, _BASE)


def test_non_list_items_rejected():
    with pytest.raises(TimelineError):
        build_render_plan(
            _tl({"kind": "video", "items": "nope"}),
            _BASE,
        )


def test_to_dict_round_trip():
    plan = build_render_plan(
        _tl({"kind": "video", "items": [{"start": 0, "end": 4, "trim_start": 1}]}),
        _BASE,
    )
    d = plan.to_dict()
    assert d["duration"] == 4
    assert d["video"][0]["trim_start"] == 1
    assert d["captions"] == []
    assert d["overlays"] == []
