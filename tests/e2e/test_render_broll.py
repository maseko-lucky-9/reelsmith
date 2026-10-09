"""Real renders with B-roll inserts through the bundled ffmpeg.

The base is the P0 640x360 sync source (frame-index bit block top-left, click
track), regenerated into ``tmp_path`` at 200 frames (8.3 s) with the fixture
generator's own encoder, so a 7 s clip holds two inserts and frames after
both. Inserts are synthetic: solid magenta clips with a loud tone made with
ffmpeg's lavfi sources, a green clip with a magenta centre band (cover-fit
check), or an indexed clip drawn with the generator's helpers whose bit block
sits where the cover-fit crop keeps it (loop and first-frame check).

Every check decodes the output; the expected windows come from a brute-force
exact-rational oracle: output frame k (at k/FPS) shows an insert iff
start <= k/FPS < start + duration.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from app.services import caption_track, clip_service, ffmpeg_tools, render_service
from app.services.render_service import BrollInsert
from app.services.transcription_service import WordTiming
from app.settings import _REPO_ANTON
from tests.fixtures.make_sync_fixture import FPS, _draw_frame, _encode, block_geometry
from tests.sync_checker import (
    BlockGeometry,
    click_onsets,
    frame_error,
    read_frame_indices,
)

pytestmark = pytest.mark.e2e

SRC_W, SRC_H = 640, 360
GEOMETRY = clip_service.reel_geometry(SRC_W, SRC_H)  # canvas 640x1137
CANVAS_W, CANVAS_H = GEOMETRY.canvas_size
OUT_SIZE = (640, 1136)  # even crop of the 1137-row canvas
SOURCE_FRAMES = 200  # 8.34 s
# >= 0.5 s apart; clip times 0.53 1.53 2.54 3.58 4.62 5.66 6.71 (two in windows)
CLICK_FRAMES = (37, 61, 85, 110, 135, 160, 185)
START, DURATION = 1.01, 7.0
FRAMES = math.ceil(DURATION * FPS)  # 168 output frames
# Both windows start later than they last (T > D): an insert left at its own
# pts 0 has already ended (or frozen) by then.
WINDOWS = ((2.0, 1.0), (5.0, 1.0))
# the base's bit block in the letterboxed inset (y = 388)
INSET = block_geometry(SRC_W).transformed(1, 1, 0, render_service.inset_y(GEOMETRY))

WORDS = [
    WordTiming(word="alpha", start=0.30, end=0.80),
    WordTiming(word="beta", start=0.90, end=1.40),
    WordTiming(word="gamma", start=2.20, end=2.70),
    WordTiming(word="delta", start=2.75, end=2.95),
    WordTiming(word="eps", start=5.20, end=5.60),
    WordTiming(word="zeta", start=5.65, end=5.95),
]

# Pixels (x, y) spread over the canvas, clear of the caption band.
POINTS = [
    (4, 4), (635, 4), (320, 200), (100, 450), (320, 568),
    (540, 700), (320, 880), (4, 1131), (635, 1131),
]  # fmt: skip


def _window_frames(start: float, duration: float, frames: int = FRAMES) -> set[int]:
    t0 = Fraction(str(start))
    t1 = t0 + Fraction(str(duration))
    return {k for k in range(frames) if t0 <= k / FPS < t1}


IN_WINDOWS = set().union(*(_window_frames(s, d) for s, d in WINDOWS))


@pytest.fixture(autouse=True)
def _bundled_anton(monkeypatch):
    from app.services import subtitle_image_service

    monkeypatch.setattr(subtitle_image_service.settings, "font_path", str(_REPO_ANTON))


# ── synthetic media ───────────────────────────────────────────────────────────


def _ffmpeg(*args: str) -> None:
    ffmpeg_tools.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    )


def _lavfi_insert(
    path: Path, colour: str, size: str, *, seconds: float, draw: str = ""
) -> Path:
    """A 30 fps solid-colour lavfi clip (optionally drawn on) with a loud 440 Hz
    tone, so mapping or mixing the insert's audio would be audible."""
    video = f"color=c={colour}:s={size}:r=30:d={seconds}"
    video += f",{draw}" if draw else ""
    tone = f"aevalsrc=0.9*sin(2*PI*440*t):s=48000:d={seconds}"
    _ffmpeg(
        "-f", "lavfi", "-i", video, "-f", "lavfi", "-i", tone,
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    )  # fmt: skip
    return path


def _indexed_insert(path: Path, frames: int, block: BlockGeometry) -> Path:
    """640x360 clip at FPS whose frame i carries index i in a bit block at ``block``."""
    with av.open(str(path), "w") as container:
        video = container.add_stream("libx264", rate=FPS)
        video.width, video.height, video.pix_fmt = SRC_W, SRC_H, "yuv420p"
        video.options = {"preset": "veryfast", "crf": "18", "bf": "3", "g": "48"}
        for index in range(frames):
            frame = av.VideoFrame.from_ndarray(
                _draw_frame(index, SRC_W, SRC_H, block), format="rgb24"
            )
            frame.pts = index
            container.mux(video.encode(frame))
        container.mux(video.encode(None))
    return path


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("broll_media")


@pytest.fixture(scope="module")
def base(media) -> Path:
    path = media / "base.mp4"
    _encode(
        path, width=SRC_W, height=SRC_H, frame_count=SOURCE_FRAMES,
        click_frames=CLICK_FRAMES, with_audio=True,
    )  # fmt: skip
    return path


@pytest.fixture(scope="module")
def magenta(media) -> Path:
    """1.5 s, 1280x720 at 30 fps: another size, aspect and rate than the clip."""
    return _lavfi_insert(
        media / "magenta.mp4", "magenta", "1280x720", seconds=1.5
    )


def _render(src: Path, out: Path, *, start=START, duration=DURATION, **kwargs) -> Path:
    render_service.render_clip(str(src), str(out), start, start + duration, **kwargs)
    return out


@pytest.fixture(scope="module")
def plain(base, media) -> Path:
    with pytest.MonkeyPatch.context() as mp:
        from app.services import subtitle_image_service

        mp.setattr(subtitle_image_service.settings, "font_path", str(_REPO_ANTON))
        return _render(base, media / "plain.mp4", word_timings=WORDS)


@pytest.fixture(scope="module")
def with_broll(base, magenta, media) -> Path:
    with pytest.MonkeyPatch.context() as mp:
        from app.services import subtitle_image_service

        mp.setattr(subtitle_image_service.settings, "font_path", str(_REPO_ANTON))
        inserts = [BrollInsert(str(magenta), s, d) for s, d in reversed(WINDOWS)]
        return _render(base, media / "broll.mp4", word_timings=WORDS, broll=inserts)


# ── decoding helpers ──────────────────────────────────────────────────────────


def _magenta(rgb: np.ndarray) -> np.ndarray:
    rgb = rgb.astype(np.int16)
    return (rgb[..., 0] > 200) & (rgb[..., 1] < 60) & (rgb[..., 2] > 200)


def _scan(path: Path, fn: Callable[[np.ndarray], object]) -> list:
    """``fn(rgb)`` for every decoded frame, in order (frames are not kept)."""
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        return [fn(f.to_ndarray(format="rgb24")) for f in container.decode(stream)]


def _points_magenta(rgb: np.ndarray) -> list[bool]:
    mask = _magenta(rgb)
    return [bool(mask[y, x]) for x, y in POINTS]


def _info(path: Path) -> dict:
    with av.open(str(path)) as container:
        v = container.streams.video[0]
        return {
            "size": (v.codec_context.width, v.codec_context.height),
            "pix_fmt": v.codec_context.pix_fmt,
            "rate": v.average_rate,
            "frames": v.frames,
            "duration": container.duration,
            "times": [float(f.time) for f in container.decode(v)],
            "audio": [a.codec_context.name for a in container.streams.audio],
        }


def _audio(path: Path) -> np.ndarray:
    with av.open(str(path)) as container:
        return np.concatenate(
            [f.to_ndarray().reshape(-1) for f in container.decode(audio=0)]
        )


# ── two solid inserts over the letterboxed reel ───────────────────────────────


def test_oracle_windows():
    assert _window_frames(2.0, 1.0) == set(range(48, 72))
    assert _window_frames(5.0, 1.0) == set(range(120, 144))


def test_inserts_cover_exactly_their_windows(with_broll):
    points = _scan(with_broll, _points_magenta)
    assert len(points) == FRAMES
    for k, flags in enumerate(points):
        if k in IN_WINDOWS:
            assert all(flags), f"frame {k} @ {k / FPS:.4f}s: insert missing at {flags}"
        else:
            assert not any(flags), f"frame {k} @ {k / FPS:.4f}s: insert outside window"
    # the edges, spelled out: last base frame, first/last insert frame, base again
    shown = [k for k, flags in enumerate(points) if all(flags)]
    assert shown == [*range(48, 72), *range(120, 144)]


def test_frame_grid_and_format_match_the_render_without_broll(plain, with_broll):
    a, b = _info(plain), _info(with_broll)
    assert b["size"] == OUT_SIZE and b["pix_fmt"] == "yuv420p"
    assert b["rate"] == a["rate"] == FPS
    assert b["frames"] == a["frames"] == FRAMES
    assert b["duration"] == a["duration"]
    assert b["times"] == a["times"]
    assert b["audio"] == a["audio"] == ["aac"]


def test_base_frames_outside_the_windows_are_unchanged_and_in_sync(plain, with_broll):
    want = read_frame_indices(plain, INSET)
    got = read_frame_indices(with_broll, INSET)
    assert [r.time for r in got] == [r.time for r in want]
    assert want[0].index == math.ceil(START * FPS) == 25
    previous = None
    for k, (g, w) in enumerate(zip(got, want)):
        assert w.index is not None, f"plain render frame {k} unreadable"
        if k in IN_WINDOWS:
            assert g.index is None, f"frame {k}: base block visible under the insert"
            previous = None
            continue
        assert g.index == w.index, f"frame {k}: {g.index} != {w.index}"
        assert abs(frame_error(g.index, g.time, FPS, source_start=START)) < 1, k
        if previous is not None:
            assert g.index == previous + 1, f"frame {k}: skipped or duplicated"
        previous = g.index


def test_audio_is_the_source_audio_only(plain, with_broll):
    samples = _audio(with_broll)
    assert np.array_equal(samples, _audio(plain))
    expected = [k / float(FPS) - START for k in CLICK_FRAMES]
    onsets = click_onsets(with_broll)
    assert len(onsets) == len(expected) == 7
    for got, want in zip(onsets, expected):
        assert abs(got - want) <= 1024 / 48000, (got, want)
    # two of the clicks play while an insert (with its own loud tone) is shown
    in_window = [t for t in expected if any(s <= t < s + d for s, d in WINDOWS)]
    assert len(in_window) == 2


def test_captions_stay_on_top_of_the_inserts(with_broll, tmp_path):
    entries = clip_service.caption_entries(WORDS, None, 3)
    track = caption_track.build_caption_track(entries, GEOMETRY, DURATION, tmp_path)
    assert track is not None
    x0, y0, x1, y1 = track.x, track.y, track.x + track.width, track.y + track.height

    def band(rgb: np.ndarray) -> tuple[float, float]:
        mask = _magenta(rgb)
        inside = mask[y0:y1, x0:x1]
        outside = np.concatenate([mask[: y0 - 4].ravel(), mask[y1 + 4 :].ravel()])
        return float(1 - inside.mean()), float(outside.mean())

    stats = _scan(with_broll, band)
    captioned = [
        k
        for k in IN_WINDOWS
        if any(e.start <= k / FPS < e.start + e.duration for e in entries)
    ]
    assert len(captioned) >= 40
    for k in captioned:
        not_magenta_in_band, magenta_elsewhere = stats[k]
        assert not_magenta_in_band > 0.02, f"frame {k}: caption hidden by the insert"
        assert magenta_elsewhere > 0.99, f"frame {k}: insert does not fill the canvas"


# ── loop, cover fit and the pan crop ──────────────────────────────────────────

# The cover fit of a 640x360 insert onto the 640x1137 canvas scales it by
# 1137/360 (2021 px wide) and keeps the centre 640 columns (crop x = 690).
_FIT_W = round(CANVAS_H * SRC_W / SRC_H)  # 2021
_FIT_X = (_FIT_W - CANVAS_W) // 2  # 690
CENTRED = BlockGeometry(x=240, y=160, cell_w=20, cell_h=20)
CENTRED_OUT = CENTRED.transformed(_FIT_W / SRC_W, CANVAS_H / SRC_H, -_FIT_X, 0)


def test_short_insert_loops_from_its_first_frame_over_the_whole_window(
    base, media, tmp_path
):
    loop = 10  # 0.417 s of insert for a 1.5 s window
    insert = _indexed_insert(tmp_path / "indexed.mp4", loop, CENTRED)
    start, duration = 2.0, 1.5
    out = _render(
        base, tmp_path / "loop.mp4", duration=4.0, word_timings=[],
        broll=[BrollInsert(str(insert), start, duration)],
    )  # fmt: skip
    window = _window_frames(start, duration, math.ceil(4.0 * FPS))
    assert window == set(range(48, 84))
    inserted = read_frame_indices(out, CENTRED_OUT)
    based = read_frame_indices(out, INSET)
    first = min(window)
    for k, (ins, bas) in enumerate(zip(inserted, based)):
        if k in window:
            # insert frame 0 on the window's first frame, then plays and loops
            assert ins.index == (k - first) % loop, (k, ins.index)
            assert bas.index is None, (k, bas.index)
        else:
            # the base, on its own frame grid (source frame 25 at t=0)
            assert bas.index == 25 + k, (k, bas.index)


def test_insert_of_another_aspect_is_cover_fitted(base, tmp_path):
    # green 1280x720 with a magenta band over the columns a centred cover
    # crop keeps (437..842 of 1280); any stretch, letterbox or offset shows
    # green or black on the canvas
    insert = _lavfi_insert(
        tmp_path / "band.mp4",
        "green",
        "1280x720",
        seconds=1.2,
        draw="drawbox=x=420:y=0:w=440:h=720:color=magenta:t=fill",
    )
    out = _render(
        base, tmp_path / "fit.mp4", duration=3.0, word_timings=[],
        broll=[BrollInsert(str(insert), 1.0, 1.0)],
    )  # fmt: skip
    window = _window_frames(1.0, 1.0, math.ceil(3.0 * FPS))
    coverage = _scan(out, lambda rgb: float(_magenta(rgb).mean()))
    info = _info(out)
    assert info["size"] == OUT_SIZE
    assert info["frames"] == math.ceil(3.0 * FPS)
    for k, share in enumerate(coverage):
        if k in window:
            assert share == 1.0, f"frame {k}: only {share:.4f} of the canvas covered"
        else:
            assert share < 0.001, f"frame {k}: {share:.4f} magenta outside the window"


def test_frame_aligned_edges_are_half_open_and_sub_frame_windows_show_nothing(
    base, magenta, tmp_path
):
    # [2.002, 3.003) starts and ends exactly on frames 48 and 72: 48 shows the
    # insert, 72 does not. [3.47, 3.49) lies between frames 83 and 84.
    aligned, between = (2.002, 1.001), (3.47, 0.02)
    frames = math.ceil(4.0 * FPS)
    assert _window_frames(*aligned, frames) == set(range(48, 72))
    assert _window_frames(*between, frames) == set()
    out = _render(
        base, tmp_path / "edges.mp4", duration=4.0, word_timings=[],
        broll=[BrollInsert(str(magenta), *w) for w in (aligned, between)],
    )  # fmt: skip
    points = _scan(out, _points_magenta)
    assert len(points) == frames
    assert [k for k, flags in enumerate(points) if any(flags)] == list(range(48, 72))
    assert all(all(flags) for flags in points[48:72])


def test_broll_over_a_moving_crop_track(base, magenta, tmp_path):
    # window pinned at x=0 keeps the base's top-left bit block in view
    track = [(0.0, 0.0)]
    window_px = render_service.pan_crop(GEOMETRY)
    block = block_geometry(SRC_W).transformed(
        CANVAS_W / window_px.width, CANVAS_H / window_px.height, 0, 0
    )
    duration = 4.0
    frames = math.ceil(duration * FPS)
    plain = _render(
        base, tmp_path / "pan.mp4", duration=duration, word_timings=[],
        crop_track=track,
    )  # fmt: skip
    out = _render(
        base, tmp_path / "pan_broll.mp4", duration=duration, word_timings=[],
        crop_track=track, broll=[BrollInsert(str(magenta), 2.0, 1.0)],
    )  # fmt: skip
    window = _window_frames(2.0, 1.0, frames)
    assert _info(out)["times"] == _info(plain)["times"]
    points = _scan(out, _points_magenta)
    assert [k for k, flags in enumerate(points) if all(flags)] == sorted(window)
    assert not any(any(flags) for k, flags in enumerate(points) if k not in window)
    want, got = read_frame_indices(plain, block), read_frame_indices(out, block)
    for k, (g, w) in enumerate(zip(got, want)):
        assert w.index is not None
        assert g.index == (None if k in window else w.index), k
