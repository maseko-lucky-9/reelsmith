from fractions import Fraction
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services import clip_service, render_service
from app.services.caption_track import CaptionTrack
from app.services.render_service import _load_captions, render_clip


def test_load_captions_srt():
    with patch("app.services.render_service.pysrt") as mock_pysrt:
        mock_pysrt.open.return_value = ["sub1", "sub2"]
        result = _load_captions("/tmp/subs.srt")
    mock_pysrt.open.assert_called_once_with("/tmp/subs.srt")
    assert result == ["sub1", "sub2"]


def test_load_captions_vtt():
    with patch("app.services.render_service.WebVTT") as mock_webvtt:
        mock_instance = MagicMock()
        mock_webvtt.return_value = mock_instance
        mock_instance.read.return_value = iter(["vtt1", "vtt2"])
        result = _load_captions("/tmp/subs.vtt")
    mock_instance.read.assert_called_once_with("/tmp/subs.vtt")
    assert result == ["vtt1", "vtt2"]


def test_load_captions_unsupported_extension_raises():
    with pytest.raises(ValueError, match="Unsupported captions extension"):
        _load_captions("/tmp/subs.txt")


# ── Geometry ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "expected_y"),
    [
        ((640, 360), 388),  # (1137 - 360) // 2 = 388, already even
        ((1280, 720), 776),  # (2275 - 720) // 2 = 777 → 776 (1 px above MoviePy)
        ((1920, 1080), 1166),  # (3413 - 1080) // 2 = 1166
        ((320, 240), 164),  # (568 - 240) // 2 = 164
    ],
)
def test_inset_y_is_centred_and_even(source, expected_y):
    assert render_service.inset_y(clip_service.reel_geometry(*source)) == expected_y


# ── argv shape ────────────────────────────────────────────────────────────────

_FPS = Fraction(24000, 1001)


def _reel_argv(captions=True):
    track = (
        CaptionTrack("captions.ffconcat", "blank.png", {}, 0, 926, 640, 104)
        if captions
        else None
    )
    return render_service.build_reel_argv(
        "/src/in.mp4",
        "/out/reel.mp4",
        start=1.25,
        duration=3.5,
        fps=_FPS,
        geometry=clip_service.reel_geometry(640, 360),
        background_png="/w/background.png",
        captions=track,
        captions_dir="/w",
    )


def _after(argv, flag):
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


def test_reel_argv_trims_source_and_maps_first_audio_track():
    argv = _reel_argv()
    assert argv[0] == "ffmpeg"
    # input window on the SOURCE, and an explicit output -t (the bg loops forever)
    assert argv[argv.index("-i") - 4 : argv.index("-i") + 2] == [
        "-ss",
        "1.250000",
        "-t",
        "3.500000",
        "-i",
        "/src/in.mp4",
    ]
    assert _after(argv, "-t") == ["3.500000", "3.500000"]
    assert _after(argv, "-i") == [
        "/src/in.mp4",
        "/w/background.png",
        "/w/captions.ffconcat",
    ]
    # -safe 0 is required: the concat demuxer rejects the per-entry
    # `option framerate 1000` directive in safe mode ("option not allowed if safe")
    assert argv[argv.index("/w/captions.ffconcat") - 5 :][:5] == [
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
    ]
    assert _after(argv, "-map") == ["[v]", "0:a:0?"]
    assert _after(argv, "-r") == ["24000/1001"]
    assert argv[-1] == "/out/reel.mp4"
    for pair in (
        ("-c:v", "libx264"),
        ("-preset", "ultrafast"),
        ("-crf", "28"),
        ("-c:a", "aac"),
    ):
        assert _after(argv, pair[0]) == [pair[1]]
    assert 1 <= int(_after(argv, "-threads")[0]) <= 8


def test_reel_filtergraph():
    graph = _after(_reel_argv(), "-filter_complex")[0].split(";")
    assert graph == [
        # background decoded once, stamped at exact k/FPS in 1/lcm(24000, 1000)
        "[1:v]loop=loop=-1:size=1:start=0,settb=expr=1/24000,setpts=N*1001[bg]",
        "[0:v]setpts=PTS-STARTPTS,scale=640:-2[in]",
        "[bg][in]overlay=x=0:y=388:ts_sync_mode=nearest[b]",
        "[b][2:v]overlay=x=0:y=926:eof_action=pass[c]",
        r"[c]crop=iw-mod(iw\,2):ih-mod(ih\,2):0:0,format=yuv420p[v]",
    ]


def test_reel_filtergraph_without_captions_has_two_inputs():
    argv = _reel_argv(captions=False)
    assert _after(argv, "-i") == ["/src/in.mp4", "/w/background.png"]
    graph = _after(argv, "-filter_complex")[0].split(";")
    assert graph[-1] == r"[b]crop=iw-mod(iw\,2):ih-mod(ih\,2):0:0,format=yuv420p[v]"
    assert not any("[2:v]" in part for part in graph)


@pytest.mark.parametrize(
    ("fps", "expected"),
    [
        (Fraction(30), "settb=expr=1/3000,setpts=N*100"),
        (Fraction(25), "settb=expr=1/1000,setpts=N*40"),
        (Fraction(30000, 1001), "settb=expr=1/30000,setpts=N*1001"),
        (Fraction(1983, 66), "settb=expr=1/661000,setpts=N*22000"),  # VFR avg = 661/22
    ],
)
def test_background_timebase_is_exact_for_frames_and_milliseconds(fps, expected):
    argv = render_service.build_reel_argv(
        "s", "o", start=0, duration=1, fps=fps,
        geometry=clip_service.reel_geometry(640, 360),
        background_png="b", captions=None, captions_dir=None,
    )  # fmt: skip
    assert expected in _after(argv, "-filter_complex")[0]
    assert _after(argv, "-r") == [f"{fps.numerator}/{fps.denominator}"]


def test_trim_argv():
    argv = render_service.build_trim_argv(
        "/src/in.mp4", "/o.mp4", start=2.0, duration=1.5, fps=Fraction(30)
    )
    assert _after(argv, "-filter_complex") == [
        r"[0:v]setpts=PTS-STARTPTS,crop=iw-mod(iw\,2):ih-mod(ih\,2):0:0,format=yuv420p[v]"
    ]
    assert _after(argv, "-map") == ["[v]", "0:a:0?"]
    assert _after(argv, "-t") == ["1.500000", "1.500000"]
    assert _after(argv, "-r") == ["30/1"]


# ── render_clip wiring ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("start", "end"), [(2.0, 2.0), (3.0, 1.0), (-1.0, 2.0)])
def test_render_clip_rejects_bad_window(start, end, tmp_path):
    with pytest.raises(ValueError, match="invalid render window"):
        render_clip("/tmp/v.mp4", str(tmp_path / "o.mp4"), start, end)


def test_render_clip_without_captions_runs_trim(tmp_path):
    output = str(tmp_path / "nested" / "out.mp4")
    with (
        patch.object(render_service.ffmpeg_tools, "fps", return_value=Fraction(25)),
        patch.object(
            render_service.ffmpeg_tools,
            "run",
            side_effect=lambda argv, **_: Path(argv[-1]).write_bytes(b"mp4"),
        ) as run,
    ):
        result = render_clip("/tmp/v.mp4", output, 0.5, 5.0)
    assert result == output
    argv = run.call_args.args[0]
    assert Path(argv[-1]).parent == Path(output).parent  # temp, then os.replace
    assert Path(output).read_bytes() == b"mp4"
    assert _after(argv, "-ss") == ["0.500000"]
    assert "[0:v]setpts=PTS-STARTPTS" in _after(argv, "-filter_complex")[0]
    assert (
        run.call_args.kwargs["timeout"]
        == render_service.settings.render_timeout_seconds
    )
    assert (tmp_path / "nested").is_dir()


@pytest.mark.parametrize(
    "fps", [Fraction(30000001, 1000000), Fraction(576089600, 19266773)]
)
def test_huge_rate_is_normalised_to_a_32_bit_time_base(fps):
    import re

    argv = render_service.build_reel_argv(
        "s", "o", start=0, duration=1, fps=fps,
        geometry=clip_service.reel_geometry(640, 360),
        background_png="b", captions=None, captions_dir=None,
    )  # fmt: skip
    graph = _after(argv, "-filter_complex")[0]
    den = int(re.search(r"settb=expr=1/(\d+)", graph).group(1))
    assert den <= 2**31 - 1
    num, rate_den = map(int, _after(argv, "-r")[0].split("/"))
    rate = Fraction(num, rate_den)
    assert rate.denominator <= 1001
    assert abs(float(rate) - float(fps)) < 1e-3
    # the background grid is exactly the output rate: step ticks per frame
    step = int(re.search(r"setpts=N\*(\d+)", graph).group(1))
    assert Fraction(step, den) == 1 / rate


@pytest.mark.parametrize(
    "fps", [Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001), Fraction(30)]
)
def test_common_rates_are_not_normalised(fps):
    assert render_service.grid_rate(fps) == fps


def test_reel_argv_rejects_captions_without_their_directory():
    track = CaptionTrack("captions.ffconcat", "blank.png", {}, 0, 926, 640, 104)
    with pytest.raises(ValueError, match="captions_dir"):
        render_service.build_reel_argv(
            "s", "o", start=0, duration=1, fps=_FPS,
            geometry=clip_service.reel_geometry(640, 360),
            background_png="b", captions=track, captions_dir=None,
        )  # fmt: skip


# ── atomic output ─────────────────────────────────────────────────────────────


def test_failed_render_leaves_no_final_or_partial_file(tmp_path):
    out_dir = tmp_path / "clips"
    output = out_dir / "00_title.mp4"

    def half_written(argv, **_):
        Path(argv[-1]).write_bytes(b"partial mp4")
        raise render_service.ffmpeg_tools.FfmpegError(
            "ffmpeg failed (rc=1): boom", returncode=1, stderr_tail="boom"
        )

    with (
        patch.object(render_service.ffmpeg_tools, "fps", return_value=Fraction(25)),
        patch.object(render_service.ffmpeg_tools, "run", side_effect=half_written),
        pytest.raises(render_service.ffmpeg_tools.FfmpegError),
    ):
        render_clip("/tmp/v.mp4", str(output), 0.0, 1.0)
    assert list(out_dir.iterdir()) == []


def test_render_writes_temp_in_same_dir_then_replaces(tmp_path):
    output = tmp_path / "00_title.mp4"
    output.write_bytes(b"previous render")
    seen = []

    def fake_run(argv, **_):
        target = Path(argv[-1])
        seen.append(target)
        assert target != output and target.parent == output.parent
        assert target.suffix == ".mp4"
        assert output.read_bytes() == b"previous render"  # untouched mid-render
        target.write_bytes(b"new render")

    with (
        patch.object(render_service.ffmpeg_tools, "fps", return_value=Fraction(25)),
        patch.object(render_service.ffmpeg_tools, "run", side_effect=fake_run),
    ):
        assert render_clip("/tmp/v.mp4", str(output), 0.0, 1.0) == str(output)
    assert output.read_bytes() == b"new render"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["00_title.mp4"]
