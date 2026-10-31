"""Timeline schema v2 (spec 016, T127): ``build_render_plan`` / ``validate_timeline``.

v2 video items are source segments, captions carry words, ``audio`` items are
voice-over files inside the clip's folder, ``caption_style`` and
``mute_source`` are top level. v1 timelines plan exactly as before.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import pytest

from app.services.animated_caption_service import PRESETS
from app.services.timeline_render_service import (
    MAX_AUDIO_GAIN,
    MAX_CAPTION_WORDS,
    MAX_ITEMS,
    MAX_OUTPUT_SECONDS,
    MAX_TEXT_CHARS,
    MAX_TRACKS,
    TIMELINE_SCHEMA_VERSION,
    TimelineError,
    build_render_plan,
    resolve_audio_src,
    validate_timeline,
)

_BASE = "/render/main.mp4"
_SOURCE = "/downloads/Talk-1234/talk.mp4"
_RANGE = (0.0, 120.0)


def _v2(*tracks: dict[str, Any], **top: Any) -> dict[str, Any]:
    return {"schema_version": 2, "tracks": list(tracks), **top}


def _video(*segments: tuple[float, float]) -> dict[str, Any]:
    return {"kind": "video", "items": [{"start": s, "end": e, "src": "main"} for s, e in segments]}


def _plan(timeline: dict[str, Any], **kw: Any):
    kw.setdefault("source_path", _SOURCE)
    kw.setdefault("source_range", _RANGE)
    return build_render_plan(timeline, _BASE, **kw)


@pytest.fixture
def clip_folder(tmp_path) -> Path:
    folder = tmp_path / "Talk-1234" / "clips"
    folder.mkdir(parents=True)
    (folder / "voice.wav").write_bytes(b"RIFF")
    return folder


# ── v1 compatibility ─────────────────────────────────────────────────────────


def test_current_schema_version_is_2():
    assert TIMELINE_SCHEMA_VERSION == 2


def test_v1_timeline_plans_as_before():
    timeline = {
        "tracks": [
            {"kind": "video", "items": [{"start": 0, "end": 8, "src": "main", "trim_start": 1.5}]},
            {"kind": "caption", "items": [{"start": 0, "end": 3, "text": "hi there"}]},
            {"kind": "text-overlay", "items": [{"start": 1, "end": 9, "text": "hook", "x": 0.4, "y": 0.2}]},
        ]
    }

    plan = build_render_plan(timeline, _BASE, source_path=_SOURCE, source_range=(0.0, 1.0))

    assert plan.schema_version == 1
    assert plan.duration == 9
    (video,) = plan.video
    assert (video.start, video.end, video.src, video.trim_start) == (0, 8, _BASE, 1.5)
    assert plan.segments == [] and plan.audio == []
    assert plan.source_path is None
    assert plan.caption_style is None and plan.mute_source is False
    assert plan.captions[0].text == "hi there"
    assert (plan.overlays[0].x, plan.overlays[0].y) == (0.4, 0.2)


def test_v1_rejects_v2_only_audio_track():
    with pytest.raises(TimelineError, match="unknown track kind"):
        validate_timeline({"tracks": [{"kind": "audio", "items": []}]})


def test_v1_zero_length_placeholder_may_be_saved_but_not_planned():
    """The web editor's initial video item is ``{start: 0, end: 0}``."""
    timeline = {"tracks": [{"kind": "video", "items": [{"start": 0, "end": 0, "src": "main"}]}]}

    assert validate_timeline(timeline) == 1
    with pytest.raises(TimelineError, match="must be > start"):
        build_render_plan(timeline, _BASE)


def test_v2_zero_length_is_never_allowed():
    timeline = _v2(_video((0, 10)), {"kind": "caption", "items": [{"start": 2, "end": 2, "text": "x"}]})

    with pytest.raises(TimelineError, match="must be > start"):
        validate_timeline(timeline, source_range=_RANGE)


# ── v2 video segments ────────────────────────────────────────────────────────


def test_v2_segments_concatenate_on_the_reel_clock():
    plan = _plan(_v2(_video((10, 14), (20.5, 22.5), (30, 31))))

    assert plan.schema_version == 2
    assert [(s.start, s.end) for s in plan.segments] == [(10, 14), (20.5, 22.5), (30, 31)]
    assert [(v.start, v.end, v.trim_start) for v in plan.video] == [
        (0, 4, 10),
        (4, 6, 20.5),
        (6, 7, 30),
    ]
    assert {v.src for v in plan.video} == {_SOURCE}
    assert plan.duration == 7
    assert plan.source_path == _SOURCE


def test_v2_segments_without_a_known_source_path_keep_main():
    plan = _plan(_v2(_video((0, 5))), source_path=None)

    assert plan.video[0].src == "main"


def test_v2_segment_touching_the_source_end_is_clamped():
    plan = _plan(_v2(_video((110, 120.0005))))

    assert plan.segments[0].end == 120.0


def test_v2_without_a_source_range_only_checks_sign_and_order():
    plan = _plan(_v2(_video((500, 510))), source_range=None)

    assert plan.duration == 10


@pytest.mark.parametrize(
    ("segments", "message"),
    [
        ([(-1, 5)], "must not be negative"),
        ([(5, 3)], "must be > start"),
        ([(5, 5.01)], "shorter than"),
        ([(100, 121)], "outside the source"),
        ([(10, 20), (15, 25)], "must not overlap"),
        ([(20, 30), (0, 10)], "source order"),
    ],
)
def test_v2_bad_segments_rejected(segments, message):
    with pytest.raises(TimelineError, match=message):
        validate_timeline(_v2(_video(*segments)), source_range=_RANGE)


def test_v2_segment_before_a_clip_window_range_rejected():
    with pytest.raises(TimelineError, match="outside the source"):
        validate_timeline(_v2(_video((90, 105))), source_range=(100.0, 110.0))


def test_v2_needs_a_segment():
    with pytest.raises(TimelineError, match="at least one video segment"):
        validate_timeline(_v2(_video()), source_range=_RANGE)


def test_v2_segment_src_must_be_main():
    timeline = _v2({"kind": "video", "items": [{"start": 0, "end": 5, "src": "/etc/passwd"}]})

    with pytest.raises(TimelineError, match="only 'main'"):
        validate_timeline(timeline, source_range=_RANGE)


def test_v2_too_many_segments_rejected():
    many = [(i, i + 0.5) for i in range(MAX_ITEMS["video"] + 1)]

    with pytest.raises(TimelineError, match="too many video items"):
        validate_timeline(_v2(_video(*many)), source_range=None)


def test_v2_total_duration_capped():
    half = MAX_OUTPUT_SECONDS / 2
    timeline = _v2(_video((0, half), (half, MAX_OUTPUT_SECONDS + 1)))

    with pytest.raises(TimelineError, match="longer than"):
        validate_timeline(timeline, source_range=None)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, True, "soon", None])
def test_non_finite_or_non_numeric_times_rejected(bad):
    timeline = _v2({"kind": "video", "items": [{"start": 0, "end": bad}]})

    with pytest.raises(TimelineError, match="number"):
        validate_timeline(timeline, source_range=_RANGE)


def test_v1_non_finite_time_rejected_too():
    with pytest.raises(TimelineError, match="finite"):
        validate_timeline({"tracks": [{"kind": "caption", "items": [{"start": 0, "end": math.inf}]}]})


def test_v1_negative_trim_start_rejected():
    timeline = {"tracks": [{"kind": "video", "items": [{"start": 0, "end": 5, "trim_start": -2}]}]}

    with pytest.raises(TimelineError, match="trim_start"):
        validate_timeline(timeline)


def test_too_many_tracks_rejected():
    timeline = _v2(_video((0, 5)), *[{"kind": "caption", "items": []}] * MAX_TRACKS)

    with pytest.raises(TimelineError, match="tracks"):
        validate_timeline(timeline, source_range=_RANGE)


@pytest.mark.parametrize("version", [3, 0, "2", True, 2.5])
def test_unsupported_schema_version_rejected(version):
    with pytest.raises(TimelineError, match="schema_version"):
        validate_timeline({"schema_version": version, "tracks": []})


# ── v2 items on the reel clock ───────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["caption", "text-overlay"])
def test_v2_item_past_the_reel_end_rejected(kind):
    timeline = _v2(_video((10, 14)), {"kind": kind, "items": [{"start": 3, "end": 5, "text": "late"}]})

    with pytest.raises(TimelineError, match="past the edited reel's end"):
        validate_timeline(timeline, source_range=_RANGE)


@pytest.mark.parametrize("kind", ["caption", "text-overlay"])
def test_oversize_or_nul_text_rejected(kind):
    for text, message in (("x" * (MAX_TEXT_CHARS + 1), "longer than"), ("a\x00b", "NUL")):
        timeline = _v2(_video((0, 10)), {"kind": kind, "items": [{"start": 0, "end": 2, "text": text}]})
        with pytest.raises(TimelineError, match=message):
            validate_timeline(timeline, source_range=_RANGE)


def test_text_at_the_limit_accepted():
    timeline = _v2(
        _video((0, 10)), {"kind": "caption", "items": [{"start": 0, "end": 2, "text": "x" * MAX_TEXT_CHARS}]}
    )

    assert validate_timeline(timeline, source_range=_RANGE) == 2


@pytest.mark.parametrize("font_size", [0, 5, 401, 10_000])
def test_overlay_font_size_bounded(font_size):
    item = {"start": 0, "end": 2, "text": "t", "x": 0.5, "y": 0.5, "font_size": font_size}
    timeline = _v2(_video((0, 10)), {"kind": "text-overlay", "items": [item]})

    with pytest.raises(TimelineError, match="font_size"):
        validate_timeline(timeline, source_range=_RANGE)


def test_too_many_overlays_rejected():
    items = [{"start": 0, "end": 1, "text": "t"}] * (MAX_ITEMS["text-overlay"] + 1)
    timeline = _v2(_video((0, 10)), {"kind": "text-overlay", "items": items})

    with pytest.raises(TimelineError, match="too many text-overlay items"):
        validate_timeline(timeline, source_range=_RANGE)


# ── caption words ────────────────────────────────────────────────────────────


def _caption_plan(item: dict[str, Any]):
    return _plan(_v2(_video((0, 10)), {"kind": "caption", "items": [item]}))


def test_same_word_count_keeps_timings_and_takes_the_edited_spelling():
    words = [
        {"word": "we", "start": 1.0, "end": 1.2},
        {"word": "sea", "start": 1.5, "end": 1.9},
        {"word": "it", "start": 2.4, "end": 2.6},
    ]

    plan = _caption_plan({"start": 1.0, "end": 3.0, "text": "We saw it", "words": words})

    assert [(w.word, w.start, w.end) for w in plan.captions[0].words] == [
        ("We", 1.0, 1.2),
        ("saw", 1.5, 1.9),
        ("it", 2.4, 2.6),
    ]


def test_same_word_count_clamps_timings_into_the_caption():
    words = [{"word": "a", "start": 0.2, "end": 1.4}, {"word": "b", "start": 2.8, "end": 3.5}]

    plan = _caption_plan({"start": 1.0, "end": 3.0, "text": "a b", "words": words})

    assert [(w.start, w.end) for w in plan.captions[0].words] == [(1.0, 1.4), (2.8, 3.0)]


def test_changed_word_count_spreads_words_evenly():
    words = [{"word": "we", "start": 1.0, "end": 1.2}, {"word": "sea", "start": 1.5, "end": 2.9}]

    plan = _caption_plan({"start": 1.0, "end": 3.0, "text": "we saw the sea", "words": words})

    got = [(w.word, w.start, w.end) for w in plan.captions[0].words]
    assert got == [
        ("we", 1.0, pytest.approx(1.5)),
        ("saw", pytest.approx(1.5), pytest.approx(2.0)),
        ("the", pytest.approx(2.0), pytest.approx(2.5)),
        ("sea", pytest.approx(2.5), pytest.approx(3.0)),
    ]


def test_caption_without_words_spreads_its_text():
    plan = _caption_plan({"start": 0.0, "end": 2.0, "text": "one two"})

    assert [(w.start, w.end) for w in plan.captions[0].words] == [(0.0, 1.0), (1.0, 2.0)]


def test_empty_caption_text_has_no_words():
    plan = _caption_plan({"start": 0.0, "end": 2.0, "text": "  ", "words": [{"word": "x", "start": 0, "end": 1}]})

    assert plan.captions[0].words == ()


@pytest.mark.parametrize(
    ("words", "message"),
    [
        ("we saw", "must be a list"),
        (["we"], r"words\[0\] must be an object"),
        ([{"word": "we", "start": math.nan, "end": 1}], "finite"),
        ([{"word": "w", "start": 0, "end": 0.01}] * (MAX_CAPTION_WORDS + 1), "more than"),
    ],
)
def test_bad_caption_words_rejected(words, message):
    timeline = _v2(
        _video((0, 10)), {"kind": "caption", "items": [{"start": 0, "end": 2, "text": "we", "words": words}]}
    )

    with pytest.raises(TimelineError, match=message):
        validate_timeline(timeline, source_range=_RANGE)


# ── caption_style / mute_source ──────────────────────────────────────────────


@pytest.mark.parametrize("style", sorted(PRESETS))
def test_every_preset_is_a_valid_caption_style(style):
    plan = _plan(_v2(_video((0, 5)), caption_style=style, mute_source=True))

    assert plan.caption_style == style
    assert plan.mute_source is True


@pytest.mark.parametrize("style", ["neon", "", "HORMOZI", 3])
def test_unknown_caption_style_rejected(style):
    with pytest.raises(TimelineError, match="caption_style"):
        validate_timeline(_v2(_video((0, 5)), caption_style=style), source_range=_RANGE)


def test_null_caption_style_and_default_mute():
    plan = _plan(_v2(_video((0, 5)), caption_style=None))

    assert plan.caption_style is None and plan.mute_source is False


@pytest.mark.parametrize("mute", ["yes", 1, 0])
def test_non_bool_mute_source_rejected(mute):
    with pytest.raises(TimelineError, match="mute_source"):
        validate_timeline(_v2(_video((0, 5)), mute_source=mute), source_range=_RANGE)


# ── audio (voice-over) ───────────────────────────────────────────────────────


def _audio_timeline(**item: Any) -> dict[str, Any]:
    return _v2(_video((0, 10)), {"kind": "audio", "items": [{"start": 0, "end": 4, "src": "voice.wav", **item}]})


def test_audio_resolves_inside_the_clip_folder(clip_folder):
    plan = _plan(_audio_timeline(gain=1.5), clip_folder=clip_folder)

    (audio,) = plan.audio
    assert audio.path == str((clip_folder / "voice.wav").resolve())
    assert (audio.src, audio.gain, audio.start, audio.end) == ("voice.wav", 1.5, 0, 4)


def test_audio_gain_defaults_to_one(clip_folder):
    timeline = _v2(_video((0, 10)), {"kind": "audio", "items": [{"start": 0, "end": 4, "src": "voice.wav"}]})

    assert _plan(timeline, clip_folder=clip_folder).audio[0].gain == 1.0


@pytest.mark.parametrize("gain", [-0.1, MAX_AUDIO_GAIN + 0.01, math.nan])
def test_audio_gain_bounded(clip_folder, gain):
    with pytest.raises(TimelineError, match="gain"):
        validate_timeline(_audio_timeline(gain=gain), source_range=_RANGE, clip_folder=clip_folder)


def test_audio_gain_bounds_inclusive(clip_folder):
    for gain in (0, MAX_AUDIO_GAIN):
        validate_timeline(_audio_timeline(gain=gain), source_range=_RANGE, clip_folder=clip_folder)


@pytest.mark.parametrize(
    "src",
    [
        "/etc/passwd.wav",
        "..",
        ".",
        "../voice.wav",
        "sub/voice.wav",
        "sub\\voice.wav",
        "voice\x00.wav",
        "x" * 252 + ".wav",
    ],
)
def test_audio_src_must_be_a_plain_name(clip_folder, src):
    with pytest.raises(TimelineError, match="must be a plain file name"):
        validate_timeline(_audio_timeline(src=src), source_range=_RANGE, clip_folder=clip_folder)


@pytest.mark.parametrize("src", ["", None, 7])
def test_audio_src_must_be_a_string(clip_folder, src):
    with pytest.raises(TimelineError, match="expected a file name"):
        validate_timeline(_audio_timeline(src=src), source_range=_RANGE, clip_folder=clip_folder)


def test_audio_src_bad_extension_rejected(clip_folder):
    (clip_folder / "notes.txt").write_text("x")

    with pytest.raises(TimelineError, match="unsupported type"):
        validate_timeline(_audio_timeline(src="notes.txt"), source_range=_RANGE, clip_folder=clip_folder)


def test_audio_src_missing_file_rejected(clip_folder):
    with pytest.raises(TimelineError, match="no such file"):
        validate_timeline(_audio_timeline(src="other.wav"), source_range=_RANGE, clip_folder=clip_folder)


def test_audio_src_directory_rejected(clip_folder):
    (clip_folder / "dir.wav").mkdir()

    with pytest.raises(TimelineError, match="not a regular file"):
        validate_timeline(_audio_timeline(src="dir.wav"), source_range=_RANGE, clip_folder=clip_folder)


def test_audio_src_symlink_escaping_the_folder_rejected(clip_folder, tmp_path):
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"RIFF")
    os.symlink(outside, clip_folder / "escape.wav")

    with pytest.raises(TimelineError, match="outside the clip's folder"):
        validate_timeline(_audio_timeline(src="escape.wav"), source_range=_RANGE, clip_folder=clip_folder)


def test_audio_without_a_clip_folder_rejected():
    with pytest.raises(TimelineError, match="no folder"):
        validate_timeline(_audio_timeline(), source_range=_RANGE, clip_folder=None)


def test_audio_past_the_reel_end_rejected(clip_folder):
    with pytest.raises(TimelineError, match="past the edited reel's end"):
        validate_timeline(_audio_timeline(end=12), source_range=_RANGE, clip_folder=clip_folder)


def test_too_many_audio_items_rejected(clip_folder):
    items = [{"start": 0, "end": 1, "src": "voice.wav"}] * (MAX_ITEMS["audio"] + 1)
    timeline = _v2(_video((0, 10)), {"kind": "audio", "items": items})

    with pytest.raises(TimelineError, match="too many audio items"):
        validate_timeline(timeline, source_range=_RANGE, clip_folder=clip_folder)


def test_resolve_audio_src_returns_the_resolved_file(clip_folder):
    roundabout = clip_folder / ".." / "clips"

    assert resolve_audio_src(roundabout, "voice.wav") == (clip_folder / "voice.wav").resolve()


# ── plan normalisation ───────────────────────────────────────────────────────


def test_v2_plan_to_dict(clip_folder):
    timeline = _v2(
        _video((10, 14), (20, 22)),
        {"kind": "caption", "items": [{"start": 0, "end": 2, "text": "hi you", "style": "default"}]},
        {"kind": "text-overlay", "items": [{"start": 1, "end": 3, "text": "hook", "x": 0.5, "y": 0.1}]},
        {"kind": "audio", "items": [{"start": 0.5, "end": 5.5, "src": "voice.wav", "gain": 2}]},
        caption_style="karaoke",
        mute_source=True,
    )

    d = _plan(timeline, clip_folder=clip_folder).to_dict()

    assert d == {
        "duration": 6,
        "video": [
            {"start": 0, "end": 4, "src": _SOURCE, "trim_start": 10},
            {"start": 4, "end": 6, "src": _SOURCE, "trim_start": 20},
        ],
        "captions": [
            {
                "start": 0,
                "end": 2,
                "text": "hi you",
                "style": "default",
                "words": [{"word": "hi", "start": 0, "end": 1}, {"word": "you", "start": 1, "end": 2}],
            }
        ],
        "overlays": [
            {
                "start": 1,
                "end": 3,
                "text": "hook",
                "x": 0.5,
                "y": 0.1,
                "font_size": 36,
                "color": "#ffffff",
            }
        ],
        "schema_version": 2,
        "segments": [{"start": 10, "end": 14}, {"start": 20, "end": 22}],
        "audio": [{"start": 0.5, "end": 5.5, "src": "voice.wav", "gain": 2}],
        "caption_style": "karaoke",
        "mute_source": True,
    }


def test_validate_timeline_reports_the_version(clip_folder):
    assert validate_timeline(_audio_timeline(), source_range=_RANGE, clip_folder=clip_folder) == 2
    assert validate_timeline({"tracks": []}) == 1
