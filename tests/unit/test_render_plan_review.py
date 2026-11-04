"""Plan render review fixes (spec 016 Phase 4a): voice-over inputs are read
with ``-protocol_whitelist file``, zero-gain voice-overs never duck the
source, every ``amix`` keeps ``normalize=0:duration=first``, and a v1 plan
whose video items leave a gap is refused."""

from __future__ import annotations

from fractions import Fraction

import pytest

from app.services import clip_service
from app.services.render_service import SourceWindow, VoiceOver, build_plan_argv, plan_segments
from app.services.timeline_render_service import build_render_plan

_FPS = Fraction(24000, 1001)
_GEOMETRY = clip_service.reel_geometry(640, 360)
_TWO = (SourceWindow(1.0, 2.5), SourceWindow(4.0, 5.25))


def _argv(**kw):
    args = {
        "segments": _TWO,
        "fps": _FPS,
        "geometry": _GEOMETRY,
        "background_png": "/w/background.png",
        "captions": None,
        "captions_dir": None,
        "source_has_audio": True,
    }
    args.update(kw)
    return build_plan_argv("/src/in.mp4", "/out/reel.mp4", **args)


def _graph(argv):
    return argv[argv.index("-filter_complex") + 1].split(";")


def test_every_voiceover_input_is_restricted_to_the_file_protocol():
    argv = _argv(voiceovers=(VoiceOver("/clip/vo.wav", 0.5, 1.75), VoiceOver("/clip/vo2.wav", 2.0, 2.5)))
    for path in ("/clip/vo.wav", "/clip/vo2.wav"):
        i = argv.index(path)
        assert argv[i - 3 : i] == ["-protocol_whitelist", "file", "-i"]
    # Only the voice-overs: the source and the background are unchanged.
    assert argv.count("-protocol_whitelist") == 2
    src = argv.index("/src/in.mp4")
    assert argv[src - 1] == "-i" and argv[src - 2] != "file"


def test_a_zero_gain_voiceover_never_ducks_the_source():
    argv = _argv(voiceovers=(VoiceOver("/clip/vo.wav", 0.5, 1.75, 0.0),))
    graph = _graph(argv)
    assert graph[-3:] == [
        "[2:a]atrim=end=1.25,asetpts=PTS-STARTPTS,volume=0,adelay=delays=500:all=1[vo0]",
        "[sa]apad=whole_dur=2.75[base]",
        "[base][vo0]amix=inputs=2:duration=first:normalize=0[a]",
    ]


def test_mixed_gains_duck_only_while_an_audible_voiceover_plays():
    argv = _argv(voiceovers=(VoiceOver("/clip/mute.wav", 0.0, 1.0, 0.0), VoiceOver("/clip/vo.wav", 2.0, 2.5, 1.0)))
    graph = _graph(argv)
    assert graph[-4:] == [
        "[2:a]atrim=end=1,asetpts=PTS-STARTPTS,volume=0,adelay=delays=0:all=1[vo0]",
        "[3:a]atrim=end=0.5,asetpts=PTS-STARTPTS,volume=1,adelay=delays=2000:all=1[vo1]",
        "[sa]apad=whole_dur=2.75,volume=volume='if(between(t,2,2.5),0.25,1)':eval=frame[base]",
        "[base][vo0][vo1]amix=inputs=3:duration=first:normalize=0[a]",
    ]


@pytest.mark.parametrize(
    "kw",
    [
        {},
        {"mute_source": True},
        {"source_has_audio": False},
        {"segments": (SourceWindow(1.0, 2.5),)},
    ],
    ids=["ducked", "muted", "silent-source", "one-segment"],
)
def test_every_amix_keeps_levels_and_the_reel_length(kw):
    argv = _argv(voiceovers=(VoiceOver("/clip/vo.wav", 0.0, 1.0, 0.0), VoiceOver("/clip/v2.wav", 0.2, 0.9)), **kw)
    mixes = [f for f in _graph(argv) if "amix=" in f]
    assert mixes
    for mix in mixes:
        assert "normalize=0" in mix and "duration=first" in mix


def _v1_plan(items):
    return build_render_plan({"tracks": [{"kind": "video", "items": items}]}, "/job/source.mp4")


def test_a_v1_plan_with_a_gap_between_video_items_is_unplannable():
    plan = _v1_plan(
        [
            {"start": 0, "end": 2, "src": "main", "trim_start": 3},
            {"start": 3, "end": 4, "src": "main", "trim_start": 8},
        ]
    )
    with pytest.raises(ValueError, match="unplannable edit"):
        plan_segments(plan, "/job/source.mp4")


def test_a_v1_plan_starting_after_zero_is_unplannable():
    plan = _v1_plan([{"start": 1, "end": 2, "src": "main", "trim_start": 3}])
    with pytest.raises(ValueError, match="unplannable edit"):
        plan_segments(plan, "/job/source.mp4")


def test_back_to_back_v1_items_still_plan():
    plan = _v1_plan(
        [
            {"start": 0, "end": 2, "src": "main", "trim_start": 3},
            {"start": 2.0005, "end": 3.0005, "src": "main", "trim_start": 8},
        ]
    )
    assert plan_segments(plan, "/job/source.mp4") == [SourceWindow(3.0, 5.0), SourceWindow(8.0, 9.0)]
