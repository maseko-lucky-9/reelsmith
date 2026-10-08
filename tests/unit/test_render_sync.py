"""Real renders of the P0 sync fixtures through the one-pass ffmpeg renderer.

Every check decodes the produced mp4 (no mocks):

* video: every frame carries the inset with a readable source frame index,
  frame 0 included, on time (``sync_checker.assert_av_sync``) and with no
  duplicated or skipped source frame;
* audio: every click lands within one AAC frame of its source time, measured
  from the render's ``-ss`` chapter start;
* captions: every frame's caption band shows exactly the caption
  ``clip_service.caption_entries`` schedules at that time — pixel-exact (±2
  luma) on a lossless encode of the same filtergraph, nearest-match on the
  production CRF 28 encode;
* format: yuv420p, even height (the 640 fixture's odd 640x1137 canvas is
  cropped to 1136), constant frame rate, sources without audio render.
"""

from __future__ import annotations

import dataclasses
import math
import subprocess
from fractions import Fraction
from pathlib import Path

import av
import imageio_ffmpeg
import numpy as np
import pytest
from PIL import Image

from app.services import caption_track, clip_service, render_service
from app.services.transcription_service import WordTiming
from app.settings import _REPO_ANTON
from tests.fixtures.make_sync_fixture import SyncFixture
from tests.sync_checker import (
    CaptionBand,
    assert_av_sync,
    assert_caption_band,
    inset_geometry,
    read_frame_indices,
)


def _w(word: str, start: float, end: float) -> WordTiming:
    return WordTiming(word=word, start=start, end=end)


# Chapter-relative word timings (Whisper-like 10 ms grid). Starts sit off the
# 1/25 s grid and >= 3 ms away from every output frame time, so millisecond
# caption timing is observable and a frame never straddles a boundary.
WORDS_640 = [
    _w("alpha", 0.13, 0.40),
    _w("beta", 0.53, 0.81),
    _w("gamma", 0.87, 1.19),
    _w("delta", 1.23, 1.50),
    _w("eps", 1.63, 1.70),
    _w("zeta", 1.73, 1.95),  # bridged to the next start across a 0.36 s gap
    _w("eta", 2.31, 2.55),
    _w("theta", 2.61, 2.83),  # last caption ends 0.17 s before the chapter
]
# frac(1.01 * 23.976) = 0.22 < 0.5: the first kept frame (25, pts 1.043 s) is
# MORE than half a frame after the chapter start, so dropping
# setpts=PTS-STARTPTS shows up as a duplicated first frame.
START_640, DURATION_640 = 1.01, 3.0

WORDS_720 = [
    _w("one", 0.07, 0.31),
    _w("two", 0.36, 0.62),
    _w("three", 0.66, 1.01),
    _w("four", 1.12, 1.44),
    _w("five", 1.47, 1.69),
]
START_720, DURATION_720 = 0.4, 2.0

LOSSLESS = ("-c:v", "libx264", "-preset", "ultrafast", "-qp", "0")


@pytest.fixture(autouse=True)
def _bundled_anton(monkeypatch):
    from app.services import subtitle_image_service

    monkeypatch.setattr(subtitle_image_service.settings, "font_path", str(_REPO_ANTON))


def _render(
    fixture_path: Path,
    out: Path,
    start: float,
    duration: float,
    words,
    *,
    lossless: bool = False,
    monkeypatch=None,
) -> Path:
    if lossless:
        monkeypatch.setattr(render_service, "_VIDEO_CODEC_ARGS", LOSSLESS)
    render_service.render_clip(
        str(fixture_path),
        str(out),
        start,
        start + duration,
        word_timings=words,
        caption_words_per_segment=3,
    )
    if lossless:
        monkeypatch.undo()
    return out


def _caption_band(
    fixture: SyncFixture, start: float, duration: float, words, work: Path
) -> CaptionBand:
    """Independent expectation for the caption band of a render."""
    geometry = clip_service.reel_geometry(fixture.width, fixture.height)
    entries = clip_service.caption_entries(words, None, 3)
    work.mkdir(parents=True, exist_ok=True)
    track = caption_track.build_caption_track(entries, geometry, duration, work)
    assert track is not None
    rect = (track.x, track.y, track.x + track.width, track.y + track.height)
    bg = render_service.background_still(str(fixture.path), start, duration, geometry)
    return CaptionBand(
        rect=rect,
        background=np.asarray(bg.convert("RGB").crop(rect)),
        images={
            key: np.asarray(Image.open(work / name))
            for key, name in track.images.items()
        },
        schedule=[((e.text, e.highlight), e.start, e.duration) for e in entries],
    )


def _inset(fixture: SyncFixture, canvas: tuple[int, int], inset_y: int):
    return inset_geometry(
        fixture.geometry,
        source_size=(fixture.width, fixture.height),
        canvas_size=canvas,
        inset_rect=(0, inset_y, fixture.width, fixture.height),
    )


def _video_info(path: Path) -> dict:
    with av.open(str(path)) as container:
        v = container.streams.video[0]
        return {
            "size": (v.codec_context.width, v.codec_context.height),
            "pix_fmt": v.codec_context.pix_fmt,
            "rate": v.average_rate,
            "frames": v.frames,
            "duration": container.duration / av.time_base,
            "audio": [a.codec_context.name for a in container.streams.audio],
        }


# ── 640x360, B-frames + click track ───────────────────────────────────────────


@pytest.fixture(scope="module")
def render_640(sync_fixture_640: SyncFixture, tmp_path_factory) -> Path:
    import app.services.subtitle_image_service as sis

    mp = pytest.MonkeyPatch()
    mp.setattr(sis.settings, "font_path", str(_REPO_ANTON))
    try:
        return _render(
            sync_fixture_640.path,
            tmp_path_factory.mktemp("r640") / "reel.mp4",
            START_640,
            DURATION_640,
            WORDS_640,
        )
    finally:
        mp.undo()


def test_640_reel_format(render_640: Path, sync_fixture_640: SyncFixture):
    info = _video_info(render_640)
    # canvas 640 x int(640 / (9/16)) = 640x1137, cropped to an even 1136
    assert info["size"] == (640, 1136)
    assert info["pix_fmt"] == "yuv420p"
    assert info["rate"] == sync_fixture_640.fps
    assert info["frames"] == round(DURATION_640 * sync_fixture_640.fps)
    assert info["duration"] == pytest.approx(DURATION_640, abs=0.05)
    assert info["audio"] == ["aac"]


def test_640_reel_av_sync_frame0_and_clicks(
    render_640: Path, sync_fixture_640: SyncFixture
):
    geometry = _inset(sync_fixture_640, (640, 1136), inset_y=388)
    report = assert_av_sync(
        render_640,
        geometry,
        fps=sync_fixture_640.fps,
        click_frames=sync_fixture_640.click_frames,
        source_start=START_640,
        require_contiguous=True,
    )
    readings = read_frame_indices(render_640, geometry)
    # frame 0 shows the inset: the first source frame at/after the chapter start
    first = math.ceil(START_640 * sync_fixture_640.fps)
    assert readings[0].time == 0.0
    assert readings[0].index == first == 25
    assert report.frames_checked == round(DURATION_640 * sync_fixture_640.fps)
    # clicks at source frames 37, 61, 90 fall inside [1.01, 4.01)
    assert report.clicks_checked == 3


def test_640_reel_caption_band_matches_schedule(
    render_640: Path, sync_fixture_640: SyncFixture, tmp_path
):
    band = _caption_band(
        sync_fixture_640, START_640, DURATION_640, WORDS_640, tmp_path / "oracle"
    )
    report = assert_caption_band(render_640, band, tolerance=None)
    assert report.captioned_frames > 0
    assert report.best_wrong_mean_error > 3 * report.worst_mean_error


def test_640_lossless_caption_band_is_pixel_exact(
    sync_fixture_640: SyncFixture, tmp_path, monkeypatch
):
    out = _render(
        sync_fixture_640.path,
        tmp_path / "lossless.mp4",
        START_640,
        DURATION_640,
        WORDS_640,
        lossless=True,
        monkeypatch=monkeypatch,
    )
    band = _caption_band(
        sync_fixture_640, START_640, DURATION_640, WORDS_640, tmp_path / "oracle"
    )
    report = assert_caption_band(out, band, tolerance=2.0)
    assert report.frames_checked == round(DURATION_640 * sync_fixture_640.fps)
    # 0.13 s → 2.83 s of captions at 23.976 fps
    assert 60 <= report.captioned_frames <= 66


def test_caption_band_checker_rejects_a_shifted_schedule(
    render_640: Path, sync_fixture_640: SyncFixture, tmp_path
):
    band = _caption_band(
        sync_fixture_640, START_640, DURATION_640, WORDS_640, tmp_path / "oracle"
    )
    late = dataclasses.replace(
        band, schedule=[(k, s + 0.1, d) for k, s, d in band.schedule]
    )
    with pytest.raises(AssertionError, match="expected"):
        assert_caption_band(render_640, late, tolerance=None)


# ── 1280x720, no audio stream ─────────────────────────────────────────────────


def test_720_no_audio_reel(sync_fixture_720: SyncFixture, tmp_path, monkeypatch):
    out = _render(
        sync_fixture_720.path,
        tmp_path / "reel720.mp4",
        START_720,
        DURATION_720,
        WORDS_720,
        lossless=True,
        monkeypatch=monkeypatch,
    )
    info = _video_info(out)
    # canvas 1280x2275 → 2274; inset y = even((2275 - 720) // 2) = 776
    assert info["size"] == (1280, 2274)
    assert info["pix_fmt"] == "yuv420p"
    assert info["audio"] == []
    report = assert_av_sync(
        out,
        _inset(sync_fixture_720, (1280, 2274), inset_y=776),
        fps=sync_fixture_720.fps,
        click_frames=(),
        source_start=START_720,
        require_contiguous=True,
    )
    assert report.frames_checked == round(DURATION_720 * sync_fixture_720.fps)
    band = _caption_band(
        sync_fixture_720, START_720, DURATION_720, WORDS_720, tmp_path / "oracle"
    )
    assert assert_caption_band(out, band, tolerance=2.0).captioned_frames > 0


# ── Millisecond-timestamp containers (mkv / webm) ─────────────────────────────


def test_mkv_source_with_ms_timestamps_has_no_frame_jitter(
    sync_fixture_640: SyncFixture, tmp_path
):
    """mkv/webm (most YouTube downloads) store 1 ms timestamps, so frame k sits
    up to 0.5 ms off k/fps. The inset overlay syncs to the NEAREST frame;
    the default (last <=) mode duplicated/skipped ~28 of 48 frames here."""
    mkv = tmp_path / "src.mkv"
    proc = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(sync_fixture_640.path),
            "-c",
            "copy",
            str(mkv),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = tmp_path / "reel.mp4"
    render_service.render_clip(str(mkv), str(out), 1.0, 3.0, word_timings=[])
    assert_av_sync(
        out,
        _inset(sync_fixture_640, (640, 1136), inset_y=388),
        fps=sync_fixture_640.fps,
        click_frames=sync_fixture_640.click_frames,
        source_start=1.0,
        # the mkv remux drops the mp4 edit list that hides AAC priming, so its
        # audio itself runs one AAC frame (21.3 ms) late; allow 1.5 frames
        audio_tolerance_s=1.5 * 1024 / 48_000,
        require_contiguous=True,
    )


# ── No captions at all (word_timings=None, no captions file) ──────────────────


def test_plain_trim_without_captions_keeps_source_frame(
    sync_fixture_640: SyncFixture, tmp_path
):
    out = tmp_path / "plain.mp4"
    render_service.render_clip(str(sync_fixture_640.path), str(out), 2.5, 4.5)
    # rendered via a same-dir temp + os.replace: nothing else is left behind
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plain.mp4"]
    info = _video_info(out)
    assert info["size"] == (640, 360)
    assert info["pix_fmt"] == "yuv420p"
    assert info["rate"] == Fraction(24000, 1001)
    assert_av_sync(
        out,
        sync_fixture_640.geometry,
        fps=sync_fixture_640.fps,
        click_frames=sync_fixture_640.click_frames,
        source_start=2.5,
        require_contiguous=True,
    )


def test_empty_word_list_renders_reel_without_captions(
    sync_fixture_720: SyncFixture, tmp_path, monkeypatch
):
    out = _render(
        sync_fixture_720.path,
        tmp_path / "nocaps.mp4",
        START_720,
        DURATION_720,
        [],
        lossless=True,
        monkeypatch=monkeypatch,
    )
    assert _video_info(out)["size"] == (1280, 2274)
    band = _caption_band(
        sync_fixture_720, START_720, DURATION_720, WORDS_720, tmp_path / "oracle"
    )
    report = assert_caption_band(
        out, dataclasses.replace(band, schedule=[]), tolerance=2.0
    )
    assert report.captioned_frames == 0


# ── VFR → CFR at the average rate ─────────────────────────────────────────────


def test_vfr_source_renders_cfr_at_average_rate(tmp_path):
    from app.services import ffmpeg_tools

    src = tmp_path / "vfr.mp4"
    ffmpeg_tools.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=64x48:rate=60", "-t", "2",
            "-vf", r"setpts='if(lt(N\,60)\,N/60\,1+(N-60)/20)/TB'",
            "-fps_mode", "vfr", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src),
        ]
    )  # fmt: skip
    average = ffmpeg_tools.fps(src)
    assert average != 60  # nominal rate is 60; MoviePy rendered at that
    out = tmp_path / "reel.mp4"
    render_service.render_clip(str(src), str(out), 0.0, 1.5, word_timings=[])
    assert _video_info(out)["rate"] == average
    with av.open(str(out)) as container:
        times = [float(f.time) for f in container.decode(video=0)]
    steps = np.diff(times)
    assert np.allclose(steps, float(1 / average), atol=1e-4)
    assert len(times) == round(1.5 * average)


# ── Rotated (phone) source ────────────────────────────────────────────────────


def test_rotated_source_renders_in_displayed_orientation(
    sync_fixture_640: SyncFixture, tmp_path, monkeypatch
):
    """A 90° display matrix makes the 640x360 coded source a 360x640 portrait
    clip: canvas 360 x int(360 / (9/16)) = 360x640, inset fills it."""
    from app.services import ffmpeg_tools

    src = tmp_path / "rot.mp4"
    ffmpeg_tools.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-display_rotation", "90", "-i", str(sync_fixture_640.path),
            "-c", "copy", str(src),
        ]
    )  # fmt: skip
    out = _render(
        src, tmp_path / "reel.mp4", 1.01, 1.0, [], lossless=True, monkeypatch=monkeypatch
    )
    assert _video_info(out)["size"] == (360, 640)
    with av.open(str(out)) as container:
        first = next(container.decode(video=0)).to_ndarray(format="rgb24")
    # the inset shows the auto-rotated first kept source frame (25)
    want = np.asarray(ffmpeg_tools.grab_frame(src, 25 * 1001 / 24000))
    assert first.shape == want.shape
    assert np.abs(first.astype(int) - want.astype(int)).mean() < 3.0


# ── Huge average-rate fractions (VFR) keep the time base in 32-bit range ──────


@pytest.mark.parametrize(
    "rate", [Fraction(30000001, 1000000), Fraction(576089600, 19266773)]
)
def test_huge_rate_fraction_still_renders(
    sync_fixture_720: SyncFixture, tmp_path, monkeypatch, rate
):
    """VFR averages can have huge numerators; lcm(numerator, 1000) then
    overflows ffmpeg's int time base (settb fails or approximates)."""
    from app.services import ffmpeg_tools

    monkeypatch.setattr(ffmpeg_tools, "fps", lambda _path: rate)
    out = tmp_path / "reel.mp4"
    render_service.render_clip(
        str(sync_fixture_720.path), str(out), 0.0, 2.0, word_timings=WORDS_720
    )
    info = _video_info(out)
    assert info["size"] == (1280, 2274)
    assert info["frames"] == pytest.approx(2.0 * float(rate), abs=1)
    with av.open(str(out)) as container:
        times = [float(f.time) for f in container.decode(video=0)]
    assert np.allclose(np.diff(times), 1 / float(rate), atol=1e-3)
