"""Edited-timeline (plan) render: argv / filter-graph goldens (spec 016 T128).

``build_plan_argv`` is pure, so every case is an exact golden of the graph.
``render_clip(plan=...)`` is checked with ffmpeg and the probes mocked: the
argv it would run and the caption PNGs it drew. The real render lives in
``tests/e2e/test_render_plan.py``.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from app.services import clip_service, render_service
from app.services.caption_track import CaptionTrack
from app.services.render_service import (
    OverlayImage,
    SourceWindow,
    VoiceOver,
    build_plan_argv,
    plan_caption_entries,
    plan_segments,
    segment_crop_track,
)
from app.services.subtitle_image_service import (
    CaptionStyle,
    caption_style_for_preset,
    create_subtitle_image,
)
from app.services.timeline_render_service import build_render_plan
from app.settings import _REPO_ANTON

_FPS = Fraction(24000, 1001)
_GEOMETRY = clip_service.reel_geometry(640, 360)  # canvas 640x1137, inset y 388
_BG = "[1:v]loop=loop=-1:size=1:start=0,settb=expr=1/24000,setpts=N*1001[bg]"
_EVEN = r"crop=iw-mod(iw\,2):ih-mod(ih\,2):0:0,format=yuv420p"
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


def _after(argv, flag):
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


def _graph(argv):
    return _after(argv, "-filter_complex")[0].split(";")


_TWO_SEGMENT_VIDEO = [
    _BG,
    "[0:v]split=2[sv0][sv1]",
    "[0:a]asplit=2[sa0][sa1]",
    "[sv0]trim=start=0:end=1.5,setpts=PTS-STARTPTS,scale=640:-2[g0]",
    "[sa0]atrim=start=0:end=1.5,asetpts=PTS-STARTPTS[h0]",
    "[sv1]trim=start=3:end=4.25,setpts=PTS-STARTPTS,scale=640:-2[g1]",
    "[sa1]atrim=start=3:end=4.25,asetpts=PTS-STARTPTS[h1]",
    "[g0][h0][g1][h1]concat=n=2:v=1:a=1[in][sa]",
    "[bg][in]overlay=x=0:y=388:ts_sync_mode=nearest[b]",
    f"[b]{_EVEN}[v]",
]


# ── segments ──────────────────────────────────────────────────────────────────


def test_two_segments_trim_and_concat_in_one_graph():
    argv = _argv()
    # the source is read ONCE over the segments' span
    assert _after(argv, "-i") == ["/src/in.mp4", "/w/background.png"]
    assert _after(argv, "-ss") == ["1.000000"]
    assert _after(argv, "-t") == ["4.250000", "2.750000"]  # span, then the sum
    assert _graph(argv) == _TWO_SEGMENT_VIDEO
    assert _after(argv, "-map") == ["[v]", "[sa]"]
    assert _after(argv, "-r") == ["24000/1001"]
    assert _after(argv, "-c:a") == ["aac"]
    assert argv[-1] == "/out/reel.mp4"


def test_one_segment_needs_no_split():
    graph = _graph(_argv(segments=(SourceWindow(2.0, 3.0),)))
    assert graph[1:4] == [
        "[0:v]trim=start=0:end=1,setpts=PTS-STARTPTS,scale=640:-2[g0]",
        "[0:a]atrim=start=0:end=1,asetpts=PTS-STARTPTS[h0]",
        "[g0][h0]concat=n=1:v=1:a=1[in][sa]",
    ]


def test_output_duration_is_the_sum_of_segments():
    segs = (SourceWindow(0.0, 0.4), SourceWindow(1.0, 1.35), SourceWindow(7.5, 9.0))
    argv = _argv(segments=segs)
    assert _after(argv, "-t")[-1] == "2.250000"


# ── audio ─────────────────────────────────────────────────────────────────────


def test_no_audio_source_concats_video_only_and_maps_no_audio():
    argv = _argv(source_has_audio=False)
    graph = _graph(argv)
    assert not any("[0:a]" in f or "atrim" in f for f in graph)
    assert "[g0][g1]concat=n=2:v=1:a=0[in]" in graph
    assert _after(argv, "-map") == ["[v]"]
    assert "-c:a" not in argv


def test_voiceover_is_delayed_mixed_and_ducks_the_source():
    vo = (VoiceOver("/clip/vo.wav", 0.5, 1.75, 1.5), VoiceOver("/clip/vo2.wav", 2.0, 2.5))
    argv = _argv(voiceovers=vo)
    assert _after(argv, "-i")[-2:] == ["/clip/vo.wav", "/clip/vo2.wav"]
    graph = _graph(argv)
    assert graph[: len(_TWO_SEGMENT_VIDEO)] == _TWO_SEGMENT_VIDEO
    assert graph[len(_TWO_SEGMENT_VIDEO) :] == [
        "[2:a]atrim=end=1.25,asetpts=PTS-STARTPTS,volume=1.5,adelay=delays=500:all=1[vo0]",
        "[3:a]atrim=end=0.5,asetpts=PTS-STARTPTS,volume=1,adelay=delays=2000:all=1[vo1]",
        "[sa]apad=whole_dur=2.75,volume=volume='if(between(t,0.5,1.75)+between(t,2,2.5),0.25,1)':eval=frame[base]",
        "[base][vo0][vo1]amix=inputs=3:duration=first:normalize=0[a]",
    ]
    assert _after(argv, "-map") == ["[v]", "[a]"]


def test_muted_voiceover_render_drops_the_source_audio():
    argv = _argv(voiceovers=(VoiceOver("/clip/vo.wav", 1.0, 2.0),), mute_source=True)
    graph = _graph(argv)
    assert not any("[0:a]" in f or "[sa]" in f or "atrim=start" in f for f in graph)
    assert "[g0][g1]concat=n=2:v=1:a=0[in]" in graph
    assert graph[-3:] == [
        "[2:a]atrim=end=1,asetpts=PTS-STARTPTS,volume=1,adelay=delays=1000:all=1[vo0]",
        "anullsrc=r=48000:cl=stereo,atrim=end=2.75[base]",
        "[base][vo0]amix=inputs=2:duration=first:normalize=0[a]",
    ]
    assert _after(argv, "-map") == ["[v]", "[a]"]


def test_muted_render_without_voiceover_keeps_a_silent_track():
    argv = _argv(mute_source=True)
    graph = _graph(argv)
    assert not any("[0:a]" in f for f in graph)
    assert graph[-1] == "anullsrc=r=48000:cl=stereo,atrim=end=2.75[a]"
    assert _after(argv, "-map") == ["[v]", "[a]"]


def test_voiceover_over_a_silent_source_mixes_onto_silence():
    argv = _argv(source_has_audio=False, voiceovers=(VoiceOver("/clip/vo.wav", 0.0, 1.0),))
    graph = _graph(argv)
    assert graph[-2:] == [
        "anullsrc=r=48000:cl=stereo,atrim=end=2.75[base]",
        "[base][vo0]amix=inputs=2:duration=first:normalize=0[a]",
    ]
    assert _after(argv, "-map") == ["[v]", "[a]"]


# ── captions and overlays ─────────────────────────────────────────────────────


def _track():
    return CaptionTrack("captions.ffconcat", "blank.png", {}, x=0, y=926, width=640, height=96)


def test_captions_and_text_overlays_stack_in_order():
    overlays = (
        OverlayImage("/w/overlay_000.png", 100, 40, 0.5, 2.0),
        OverlayImage("/w/overlay_001.png", 0, 600, 1, 1.5),
    )
    argv = _argv(captions=_track(), captions_dir="/w", overlays=overlays)
    assert _after(argv, "-i") == [
        "/src/in.mp4",
        "/w/background.png",
        "/w/captions.ffconcat",
        "/w/overlay_000.png",
        "/w/overlay_001.png",
    ]
    assert _graph(argv)[-4:] == [
        "[b][2:v]overlay=x=0:y=926:eof_action=pass[c]",
        "[c][3:v]overlay=x=100:y=40:enable='between(t,0.5,2)'[o0]",
        "[o0][4:v]overlay=x=0:y=600:enable='between(t,1,1.5)'[o1]",
        f"[o1]{_EVEN}[v]",
    ]


def test_text_overlay_without_captions_sits_on_the_composite():
    argv = _argv(overlays=(OverlayImage("/w/overlay_000.png", 2, 4, 0, 1),))
    assert _graph(argv)[-2:] == [
        "[b][2:v]overlay=x=2:y=4:enable='between(t,0,1)'[o0]",
        f"[o0]{_EVEN}[v]",
    ]


def test_caption_style_preset_maps_colours_and_size():
    assert caption_style_for_preset(None) == CaptionStyle()
    hormozi = caption_style_for_preset("hormozi")
    assert hormozi == CaptionStyle(
        font_size=96,
        fill=(255, 255, 255, 255),
        highlight_fill=(255, 242, 0, 255),
        stroke_fill=(0, 0, 0, 255),
        highlights=True,
    )
    assert caption_style_for_preset("mrbeast").highlight_fill == (255, 0, 102, 255)
    assert caption_style_for_preset("mrbeast").font_size == 110
    assert caption_style_for_preset("subtle").highlights is False
    with pytest.raises(ValueError):
        caption_style_for_preset("comic-sans")


def _plan(tmp_path, **extra):
    timeline = {
        "schema_version": 2,
        "tracks": [
            {"kind": "video", "items": [{"start": 1.0, "end": 2.5}, {"start": 4.0, "end": 5.25}]},
            {
                "kind": "caption",
                "items": [
                    {
                        "start": 0.2,
                        "end": 1.2,
                        "text": "hello there",
                        "words": [{"start": 0.4, "end": 0.7}, {"start": 0.7, "end": 1.1}],
                    }
                ],
            },
        ],
        **extra,
    }
    return build_render_plan(timeline, "main", source_path="/src/in.mp4", source_range=(0, 10), clip_folder=tmp_path)


def test_plan_caption_entries_follow_word_timings(tmp_path):
    plan = _plan(tmp_path)
    E = clip_service.CaptionEntry
    entries = plan_caption_entries(plan.captions, CaptionStyle())
    assert [(e.text, e.highlight) for e in entries] == [
        ("hello there", None),
        ("hello there", 0),
        ("hello there", 1),
    ]
    assert entries[0] == E("hello there", None, 0.2, pytest.approx(0.2))
    assert entries[1] == E("hello there", 0, 0.4, pytest.approx(0.3))
    assert entries[2] == E("hello there", 1, 0.7, pytest.approx(0.5))
    whole = plan_caption_entries(plan.captions, caption_style_for_preset("static"))
    assert whole == [E("hello there", None, 0.2, pytest.approx(1.0))]


def _mock_render(tmp_path, plan, *, has_audio=True, **kw):
    """render_clip(plan=...) with ffmpeg and the probes mocked; returns the
    argv and the caption PNGs it drew (read while the work dir exists)."""
    seen = {}

    def run(argv, **_):
        seen["argv"] = argv
        work = Path(_after(argv, "-i")[1]).parent
        seen["captions"] = {p.name: np.array(Image.open(p)) for p in work.glob("cap_*.png")}
        Path(argv[-1]).write_bytes(b"mp4")

    canvas = Image.new("RGB", _GEOMETRY.canvas_size)
    with (
        patch.object(render_service.ffmpeg_tools, "fps", return_value=_FPS),
        patch.object(render_service.ffmpeg_tools, "has_audio", return_value=has_audio),
        patch.object(render_service.ffmpeg_tools, "video_size", return_value=(640, 360)),
        patch.object(render_service, "background_still", return_value=canvas),
        patch.object(render_service.ffmpeg_tools, "run", side_effect=run),
    ):
        out = render_service.render_clip("/src/in.mp4", str(tmp_path / "out.mp4"), 1.0, 5.25, plan=plan, **kw)
    assert Path(out).read_bytes() == b"mp4"
    return seen


@pytest.fixture
def anton(monkeypatch):
    from app.services import subtitle_image_service

    monkeypatch.setattr(subtitle_image_service.settings, "font_path", str(_REPO_ANTON))


def _has_colour(arr, rgb):
    return bool(((arr[..., :3] == rgb).all(axis=-1) & (arr[..., 3] == 255)).any())


def test_render_clip_draws_captions_in_the_preset_colours(tmp_path, anton):
    seen = _mock_render(tmp_path, _plan(tmp_path, caption_style="karaoke"))
    graph = _graph(seen["argv"])
    assert "[b][2:v]overlay=" in graph[-2]
    assert not any("ass=" in f or "subtitles=" in f for f in graph)  # no second pass
    pngs = list(seen["captions"].values())
    assert len(pngs) == 3  # plain before the first word, then one per word
    karaoke = (0x22, 0xD3, 0xEE)
    assert sum(_has_colour(a, karaoke) for a in pngs) == 2
    assert not any(_has_colour(a, (46, 204, 64)) for a in pngs)  # not the default green


def test_render_clip_plan_default_style_keeps_the_pipeline_look(tmp_path, anton):
    seen = _mock_render(tmp_path, _plan(tmp_path))
    pngs = list(seen["captions"].values())
    assert len(pngs) == 3
    assert sum(_has_colour(a, (46, 204, 64)) for a in pngs) == 2


def test_render_clip_plan_is_one_pass_with_the_plan_segments(tmp_path, anton):
    seen = _mock_render(tmp_path, _plan(tmp_path))
    argv = seen["argv"]
    assert _after(argv, "-ss") == ["1.000000"]
    assert _after(argv, "-t")[-1] == "2.750000"
    assert "[g0][h0][g1][h1]concat=n=2:v=1:a=1[in][sa]" in _graph(argv)


def test_render_clip_overlay_text_never_reaches_the_graph(tmp_path, anton):
    hostile = "x'];[0:v]drawtext=text=%{pts}:fontfile=/etc/passwd,;[a]"
    plan = _plan(
        tmp_path,
        tracks=[
            {"kind": "video", "items": [{"start": 1.0, "end": 2.5}]},
            {
                "kind": "text-overlay",
                "items": [{"start": 0.1, "end": 1.0, "text": hostile, "x": 0.5, "y": 0.1, "color": "#ff0000"}],
            },
        ],
    )
    seen = _mock_render(tmp_path, plan)
    argv = seen["argv"]
    assert not any("drawtext" in a or "passwd" in a for a in argv)
    overlay_png = _after(argv, "-i")[2]
    assert Path(overlay_png).name == "overlay_000.png"
    assert "enable='between(t,0.1,1)'" in _graph(argv)[-2]


def test_render_clip_plan_refuses_broll(tmp_path):
    insert = render_service.BrollInsert("/b.mp4", 0.0, 1.0)
    with pytest.raises(ValueError, match="broll"):
        render_service.render_clip(
            "/src/in.mp4", str(tmp_path / "o.mp4"), 1.0, 5.25, plan=_plan(tmp_path), broll=[insert]
        )


def test_render_clip_plan_refuses_segments_outside_the_window(tmp_path):
    with pytest.raises(ValueError, match="outside the render window"):
        _mock_render_window(tmp_path, 2.0, 5.25)


def _mock_render_window(tmp_path, start, end):
    with patch.object(render_service.ffmpeg_tools, "run") as run:
        render_service.render_clip("/src/in.mp4", str(tmp_path / "o.mp4"), start, end, plan=_plan(tmp_path))
    assert not run.called


def test_render_clip_plan_without_source_audio_maps_none(tmp_path, anton):
    seen = _mock_render(tmp_path, _plan(tmp_path), has_audio=False)
    assert _after(seen["argv"], "-map") == ["[v]"]


# ── v1 plans: the client's src ────────────────────────────────────────────────


def _v1(src):
    return build_render_plan(
        {"tracks": [{"kind": "video", "items": [{"start": 0, "end": 2, "src": src, "trim_start": 3}]}]},
        "/job/base.mp4",
    )


def test_v1_foreign_src_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not this job's source video"):
        plan_segments(_v1("/etc/other.mp4"), "/job/source.mp4")
    with patch.object(render_service.ffmpeg_tools, "run") as run, pytest.raises(ValueError, match="refusing"):
        render_service.render_clip("/job/source.mp4", str(tmp_path / "o.mp4"), 0, 10, plan=_v1("/etc/other.mp4"))
    assert not run.called


def test_v1_main_src_resolves_to_the_base_clip_and_is_checked():
    # "main" means base_clip_path; rendering it from another video is refused.
    with pytest.raises(ValueError, match="not this job's source video"):
        plan_segments(_v1("main"), "/job/source.mp4")
    assert plan_segments(_v1("main"), "/job/base.mp4") == [SourceWindow(3.0, 5.0)]


def test_v1_src_equal_to_the_job_source_renders():
    assert plan_segments(_v1("/job/./source.mp4"), "/job/source.mp4") == [SourceWindow(3.0, 5.0)]


def test_v2_plan_for_another_source_is_refused(tmp_path):
    plan = _plan(tmp_path)
    with pytest.raises(ValueError, match="different source video"):
        plan_segments(plan, "/src/other.mp4")
    assert plan_segments(plan, "/src/in.mp4") == list(_TWO)


# ── crop track per segment ────────────────────────────────────────────────────


def test_segment_crop_track_rebases_the_span_track():
    track = [(0.0, 0.0), (2.0, 100.0), (2.0, 300.0), (4.0, 300.0)]
    # [1, 3) of the span: the ramp's midpoint, the hard cut at 2, the hold
    assert segment_crop_track(track, 1.0, 2.0) == [(0.0, 50.0), (1.0, 100.0), (1.0, 300.0), (2.0, 300.0)]
    # a segment starting AT the cut takes the value after it
    assert segment_crop_track(track, 2.0, 1.0) == [(0.0, 300.0), (1.0, 300.0)]
    # a segment ending AT the cut keeps the value before it
    assert segment_crop_track(track, 0.0, 2.0) == [(0.0, 0.0), (2.0, 100.0)]
    # beyond the track: held
    assert segment_crop_track(track, 5.0, 1.0) == [(0.0, 300.0), (1.0, 300.0)]


def test_segment_crop_track_matches_the_span_track_at_every_time():
    rng = np.random.default_rng(7)
    times = np.sort(rng.uniform(0, 10, 20))
    track = [(float(t), float(x)) for t, x in zip(times, rng.uniform(0, 400, 20))]
    for offset, length in ((0.0, 10.0), (2.3, 1.1), (7.9, 3.0)):
        local = segment_crop_track(track, offset, length)
        ts = np.linspace(0, length, 50, endpoint=False)
        want = np.interp(ts + offset, [t for t, _ in track], [x for _, x in track])
        got = np.interp(ts, [t for t, _ in local], [x for _, x in local])
        assert np.allclose(got, want)


def test_segment_crop_track_is_bounded():
    track = [(i / 10, float(i % 7) * 10) for i in range(render_service.MAX_CROP_KEYFRAMES)]
    local = segment_crop_track(track, 0.05, 6.4)
    assert len(local) == render_service.MAX_CROP_KEYFRAMES
    assert local[0][0] == 0.0 and local[-1][0] == pytest.approx(6.4)


def test_crop_track_is_remapped_per_segment():
    # the track was computed over a window starting at 0.5 s of source (before
    # the first segment, 1.0) and pans 0 -> 425 px over its first 4.25 s
    argv = _argv(crop_track=[(0.0, 0.0), (4.25, 425.0)], crop_origin=0.5)
    graph = _graph(argv)
    assert graph[3] == (
        "[sv0]trim=start=0:end=1.5,setpts=PTS-STARTPTS,"
        "crop=w=202:h=360:x='lt(t,0)*50+gte(t,0)*lt(t,1.5)*(50+150*(t-0)/1.5)+gte(t,1.5)*200':y=0,"
        "scale=640:1137[g0]"
    )
    assert graph[5] == (
        "[sv1]trim=start=3:end=4.25,setpts=PTS-STARTPTS,"
        "crop=w=202:h=360:x='lt(t,0)*350+gte(t,0)*lt(t,0.75)*(350+75*(t-0)/0.75)"
        "+gte(t,0.75)*lt(t,1.25)*(425+0*(t-0.75)/0.5)+gte(t,1.25)*425':y=0,"
        "scale=640:1137[g1]"
    )
    assert "[bg][in]overlay=x=0:y=0:ts_sync_mode=nearest[b]" in graph


def test_plan_without_segments_is_refused():
    with pytest.raises(ValueError):
        _argv(segments=())


def test_single_window_render_is_untouched_by_the_plan_code():
    # no plan: the existing argv builder, byte for byte (see test_render_service)
    argv = render_service.build_reel_argv(
        "/src/in.mp4",
        "/out/reel.mp4",
        start=1.25,
        duration=3.5,
        fps=_FPS,
        geometry=_GEOMETRY,
        background_png="/w/background.png",
        captions=None,
        captions_dir=None,
    )
    assert _after(argv, "-map") == ["[v]", "0:a:0?"]
    assert "concat=" not in _after(argv, "-filter_complex")[0]


def test_subtitle_image_style_kwargs_default_to_the_old_colours():
    a = create_subtitle_image("hello world", (320, 180), font_size=20, highlight_word_index=0)
    b = create_subtitle_image(
        "hello world",
        (320, 180),
        font_size=20,
        highlight_word_index=0,
        fill=(255, 255, 255, 255),
        highlight_fill=(46, 204, 64, 255),
        stroke_fill=(0, 0, 0, 255),
    )
    assert np.array_equal(a, b)
