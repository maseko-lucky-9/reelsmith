"""Self-tests for the deterministic A/V sync fixture and ``tests.sync_checker``.

The fixture generator (``tests/fixtures/make_sync_fixture.py``) and the
checker's decoder are deliberately independent implementations of the same
bit-block format, so a bug on either side turns these tests red.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import av
import imageio_ffmpeg
import numpy as np
import pytest

from tests.fixtures.make_sync_fixture import SyncFixture
from tests.sync_checker import (
    BlockGeometry,
    assert_av_sync,
    click_onsets,
    decode_index,
    frame_error,
    inset_geometry,
    read_frame_indices,
)

# One AAC frame (1024 samples) at the fixture's 48 kHz rate.
_ONE_AAC_FRAME_S = 1024 / 48_000


def _ffmpeg(*args: str) -> None:
    proc = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]


# ── Scale + pad transform (stands in for the reel reframe) ────────────────────
# 640x360 → inset scaled to 480x270, centred on a 480x854 canvas (9:16).
_CANVAS_W, _CANVAS_H = 480, 854
_INSET_W, _INSET_H = 480, 270


@pytest.fixture(scope="session")
def scaled_padded_640(sync_fixture_640: SyncFixture, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("sync_transform") / "scaled_padded.mp4"
    _ffmpeg(
        "-i",
        str(sync_fixture_640.path),
        "-vf",
        f"scale={_INSET_W}:{_INSET_H},pad={_CANVAS_W}:{_CANVAS_H}:(ow-iw)/2:(oh-ih)/2:color=gray",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(out),
    )
    return out


_TRIM_DURATION = 4.0


def _trim(source: SyncFixture, start: float, out: Path) -> Path:
    """Re-encode ``_TRIM_DURATION`` s from ``start`` (what a chapter render does)."""
    _ffmpeg(
        "-ss",
        str(start),
        "-t",
        str(_TRIM_DURATION),
        "-i",
        str(source.path),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(out),
    )
    return out


@pytest.fixture(scope="session")
def trimmed_640(sync_fixture_640: SyncFixture, tmp_path_factory) -> Path:
    """Trim at 1.0 s — 1 ms before source frame 24 (pts 1.001 s)."""
    return _trim(
        sync_fixture_640, 1.0, tmp_path_factory.mktemp("sync_trim") / "trimmed.mp4"
    )


@pytest.fixture(scope="session")
def trimmed_640_off_grid(sync_fixture_640: SyncFixture, tmp_path_factory) -> Path:
    """Trim at 1.03 s — 12.7 ms before frame 25, so ffmpeg snaps it onto t=0."""
    return _trim(
        sync_fixture_640, 1.03, tmp_path_factory.mktemp("sync_trim") / "trimmed_103.mp4"
    )


@pytest.fixture(scope="session")
def audio_delayed_640(sync_fixture_640: SyncFixture, tmp_path_factory) -> Path:
    """Audio pushed 100 ms late — a desync the checker must catch."""
    out = tmp_path_factory.mktemp("sync_delay") / "audio_delayed.mp4"
    _ffmpeg(
        "-i",
        str(sync_fixture_640.path),
        "-af",
        "adelay=100:all=1",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        str(out),
    )
    return out


@pytest.fixture(scope="session")
def video_slipped_640(sync_fixture_640: SyncFixture, tmp_path_factory) -> Path:
    """Video one frame late (first frame cloned) — the smallest slip that matters."""
    out = tmp_path_factory.mktemp("sync_slip") / "video_slipped.mp4"
    _ffmpeg(
        "-i",
        str(sync_fixture_640.path),
        "-vf",
        "tpad=start=1:start_mode=clone",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        str(out),
    )
    return out


def _centred_geometry(fixture: SyncFixture) -> BlockGeometry:
    return inset_geometry(
        fixture.geometry,
        source_size=(fixture.width, fixture.height),
        canvas_size=(_CANVAS_W, _CANVAS_H),
    )


# ── Fixture properties ────────────────────────────────────────────────────────


def test_fixture_640_stream_properties(sync_fixture_640: SyncFixture):
    with av.open(str(sync_fixture_640.path)) as container:
        video = container.streams.video[0]
        assert (video.codec_context.width, video.codec_context.height) == (640, 360)
        assert video.codec_context.name == "h264"
        assert video.average_rate == sync_fixture_640.fps
        assert sync_fixture_640.fps.numerator == 24000
        assert sync_fixture_640.fps.denominator == 1001
        assert len(container.streams.audio) == 1
        assert container.streams.audio[0].codec_context.name == "aac"
        assert container.streams.audio[0].codec_context.sample_rate == 48_000
    assert 5.5 < sync_fixture_640.duration < 6.5
    assert len(sync_fixture_640.click_frames) >= 4


def test_fixture_720_is_hd_and_has_no_audio(sync_fixture_720: SyncFixture):
    with av.open(str(sync_fixture_720.path)) as container:
        video = container.streams.video[0]
        assert (video.codec_context.width, video.codec_context.height) == (1280, 720)
        assert len(container.streams.audio) == 0
    assert sync_fixture_720.has_audio is False
    assert sync_fixture_720.click_frames == ()
    assert 2.5 < sync_fixture_720.duration < 3.5


@pytest.mark.parametrize("name", ["sync_fixture_640", "sync_fixture_720"])
def test_fixture_contains_b_frames(name: str, request):
    fixture: SyncFixture = request.getfixturevalue(name)
    with av.open(str(fixture.path)) as container:
        pict_types = [frame.pict_type for frame in container.decode(video=0)]
    with av.open(str(fixture.path)) as container:
        reordered = sum(
            1 for p in container.demux(video=0) if p.pts is not None and p.pts != p.dts
        )
    assert len(pict_types) == fixture.frame_count
    assert pict_types.count(av.video.frame.PictureType.B) > fixture.frame_count // 4
    assert reordered > 0, (
        "no packet has pts != dts — encoder emitted no reordered frames"
    )


# ── Bit-block decoding ────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["sync_fixture_640", "sync_fixture_720"])
def test_every_frame_index_decodes_on_raw_fixture(name: str, request):
    fixture: SyncFixture = request.getfixturevalue(name)
    readings = read_frame_indices(fixture.path, fixture.geometry)
    assert [r.index for r in readings] == list(range(fixture.frame_count))
    for r in readings:
        assert r.time == pytest.approx(fixture.frame_time(r.index), abs=1e-3)


def test_decode_index_rejects_frame_without_block(sync_fixture_640: SyncFixture):
    flat = np.full((360, 640), 128, dtype=np.uint8)
    assert decode_index(flat, sync_fixture_640.geometry) is None


def test_inset_geometry_scales_and_offsets_block():
    src = BlockGeometry(x=10, y=10, cell_w=20, cell_h=20, cols=8, rows=2)
    got = inset_geometry(src, source_size=(640, 360), canvas_size=(480, 854))
    # inset 480x270 centred vertically: y0 = (854 - 270) // 2 = 292
    assert got == BlockGeometry(
        x=7.5, y=292 + 7.5, cell_w=15, cell_h=15, cols=8, rows=2
    )


def test_frame_error_is_signed_offset_in_frames(sync_fixture_640: SyncFixture):
    fps = sync_fixture_640.fps
    frame = float(1 / fps)
    assert frame_error(0, 0.0, fps) == pytest.approx(0.0)
    assert frame_error(10, frame * 10, fps) == pytest.approx(0.0, abs=1e-9)
    assert frame_error(10, frame * 9.6, fps) == pytest.approx(0.4)
    assert frame_error(9, frame * 9.6, fps) == pytest.approx(-0.6)
    # MoviePy opens a 0.5 s chapter on frame 11: floor convention, inside one frame.
    assert frame_error(11, 0.0, fps, source_start=0.5) == pytest.approx(
        11 - 0.5 * 23.976, abs=1e-3
    )
    # ffmpeg -ss 1.0 opens on frame 24 (pts 1.001 s): nearest convention.
    assert frame_error(24, 0.0, fps, source_start=1.0) == pytest.approx(0.024, abs=1e-3)


# ── Audio clicks ──────────────────────────────────────────────────────────────


def test_click_onsets_match_known_frames(sync_fixture_640: SyncFixture):
    onsets = click_onsets(sync_fixture_640.path)
    expected = [sync_fixture_640.frame_time(k) for k in sync_fixture_640.click_frames]
    assert len(onsets) == len(expected), (onsets, expected)
    for got, want in zip(onsets, expected):
        assert abs(got - want) <= _ONE_AAC_FRAME_S, (got, want)


def test_click_onsets_empty_for_video_without_audio(sync_fixture_720: SyncFixture):
    assert click_onsets(sync_fixture_720.path) == []


# ── End-to-end checker ────────────────────────────────────────────────────────


def test_assert_av_sync_passes_on_raw_fixtures(
    sync_fixture_640: SyncFixture, sync_fixture_720: SyncFixture
):
    report = assert_av_sync(
        sync_fixture_640.path,
        sync_fixture_640.geometry,
        fps=sync_fixture_640.fps,
        click_frames=sync_fixture_640.click_frames,
    )
    assert report.frames_checked == sync_fixture_640.frame_count
    assert report.clicks_checked == len(sync_fixture_640.click_frames)

    report_720 = assert_av_sync(
        sync_fixture_720.path,
        sync_fixture_720.geometry,
        fps=sync_fixture_720.fps,
        click_frames=(),
    )
    assert report_720.frames_checked == sync_fixture_720.frame_count
    assert report_720.clicks_checked == 0


def test_checker_survives_scale_and_pad(
    sync_fixture_640: SyncFixture, scaled_padded_640: Path
):
    geometry = _centred_geometry(sync_fixture_640)
    readings = read_frame_indices(scaled_padded_640, geometry)
    assert [r.index for r in readings] == list(range(sync_fixture_640.frame_count))

    report = assert_av_sync(
        scaled_padded_640,
        geometry,
        fps=sync_fixture_640.fps,
        click_frames=sync_fixture_640.click_frames,
    )
    assert report.frames_checked == sync_fixture_640.frame_count
    assert report.clicks_checked == len(sync_fixture_640.click_frames)


@pytest.mark.parametrize(
    ("render", "start"), [("trimmed_640", 1.0), ("trimmed_640_off_grid", 1.03)]
)
def test_checker_honours_source_start_on_trimmed_render(
    sync_fixture_640: SyncFixture, render: str, start: float, request
):
    path: Path = request.getfixturevalue(render)
    report = assert_av_sync(
        path,
        sync_fixture_640.geometry,
        fps=sync_fixture_640.fps,
        click_frames=sync_fixture_640.click_frames,
        source_start=start,
    )
    # Clicks before the trim point (frame 12) or after its end fall outside.
    in_window = [
        k
        for k in sync_fixture_640.click_frames
        if start <= sync_fixture_640.frame_time(k) < start + _TRIM_DURATION
    ]
    assert report.clicks_checked == len(in_window) > 0
    assert report.frames_checked == pytest.approx(
        _TRIM_DURATION * float(sync_fixture_640.fps), abs=1
    )


def test_checker_rejects_wrong_source_start(
    sync_fixture_640: SyncFixture, trimmed_640: Path
):
    with pytest.raises(AssertionError, match="frame index"):
        assert_av_sync(
            trimmed_640,
            sync_fixture_640.geometry,
            fps=sync_fixture_640.fps,
            click_frames=sync_fixture_640.click_frames,
            source_start=0.0,
        )


def test_checker_rejects_delayed_audio(
    sync_fixture_640: SyncFixture, audio_delayed_640: Path
):
    with pytest.raises(AssertionError, match="click"):
        assert_av_sync(
            audio_delayed_640,
            sync_fixture_640.geometry,
            fps=sync_fixture_640.fps,
            click_frames=sync_fixture_640.click_frames,
        )


def test_checker_rejects_one_frame_video_slip(
    sync_fixture_640: SyncFixture, video_slipped_640: Path
):
    with pytest.raises(AssertionError, match=r"off by -1\.00 frames"):
        assert_av_sync(
            video_slipped_640,
            sync_fixture_640.geometry,
            fps=sync_fixture_640.fps,
            click_frames=sync_fixture_640.click_frames,
        )
