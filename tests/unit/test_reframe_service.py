"""Face-tracked crop track (FR-010 reframe): the pure parts and the frame sampler.

Faces come from test doubles (``tests/unit/fake_face_detector.py``); the real
YuNet model is only exercised by ``tests/integration/test_yunet_model.py``.
Sources are synthetic: a white block on a dark background stands in for a
face, so positions are known exactly.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pytest

from app.services import ffmpeg_tools, reframe_service
from app.services.face_detector import Face
from app.services.reframe_service import (
    Observation,
    decimate,
    hold_gaps,
    plan_crop_track,
    primary_face,
    smooth_positions,
    usable_faces,
    x_left_for,
)
from app.services.render_service import MAX_CROP_KEYFRAMES
from tests.unit.fake_face_detector import (
    BLOCK,
    BlockDetector,
    ScriptedDetector,
    write_block_video,
)

SRC = (640, 360)
WINDOW_W = 202  # pan_crop of a 640x360 source on the 9:16 canvas
MAX_X = SRC[0] - WINDOW_W  # 438
MAX_STEP = reframe_service.MAX_SPEED * WINDOW_W  # px per second
DEADBAND = reframe_service.DEADBAND * WINDOW_W


def _face(
    cx: float,
    *,
    h: float = 60,
    w: float | None = None,
    score: float = 0.9,
    cy: float = 180,
) -> Face:
    w = h if w is None else w
    return Face(x=cx - w / 2, y=cy - h / 2, w=w, h=h, score=score)


def _obs(t: float, *faces: Face) -> Observation:
    return Observation(t=t, faces=tuple(faces))


# ── face choice ───────────────────────────────────────────────────────────────


def test_primary_face_is_the_largest():
    small_central, big_left = _face(320, h=40), _face(100, h=80)

    assert primary_face([small_central, big_left], SRC[0]) is big_left


def test_near_equal_faces_prefer_the_most_central():
    left, central = _face(100, h=80), _face(330, h=78)  # 95 % of the area

    assert primary_face([left, central], SRC[0]) is central


def test_primary_face_of_nothing_is_none():
    assert primary_face([], SRC[0]) is None


def test_usable_faces_drop_low_scores_and_tiny_faces():
    min_h = reframe_service.MIN_FACE_HEIGHT * SRC[1]  # 18 px of 360
    keep = [_face(100, h=min_h), _face(300, score=reframe_service.MIN_FACE_SCORE)]
    drop = [_face(200, h=min_h - 1), _face(400, score=0.59)]

    assert usable_faces([*keep, *drop], SRC[1]) == keep


# ── positions ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("cx", "x_left"),
    [(320, 219), (101, 0), (10, 0), (539, 438), (630, 438)],
)
def test_x_left_centres_the_window_on_the_face_and_clamps(cx, x_left):
    assert x_left_for(cx, SRC[0], WINDOW_W) == x_left


def test_gaps_hold_the_last_position_and_lead_in_takes_the_first():
    assert hold_gaps([None, 5.0, None, None, 9.0, None]) == [5, 5, 5, 5, 9, 9]


def test_no_position_at_all_is_none():
    assert hold_gaps([None, None]) is None


def test_smoothing_starts_on_the_first_target():
    assert smooth_positions([0.0, 0.5], [300.0, 300.0], window_width=WINDOW_W) == [
        300.0,
        300.0,
    ]


def test_smoothing_never_moves_faster_than_the_speed_limit():
    times = [k * 0.5 for k in range(12)]
    xs = [0.0] + [float(MAX_X)] * 11  # the face jumps across the frame

    smoothed = smooth_positions(times, xs, window_width=WINDOW_W)

    steps = [b - a for a, b in zip(smoothed, smoothed[1:])]
    assert all(0 <= s <= MAX_STEP * 0.5 + 1e-9 for s in steps), steps
    assert steps[0] == pytest.approx(MAX_STEP * 0.5)  # moves, at the limit
    assert max(smoothed) <= MAX_X


def test_smoothing_ignores_jitter_inside_the_deadband():
    jitter = [100.0, 100 + DEADBAND * 0.9, 100 - DEADBAND * 0.9, 100 + DEADBAND * 0.5]

    smoothed = smooth_positions([0, 0.5, 1, 1.5], jitter, window_width=WINDOW_W)

    assert len(set(smoothed)) == 1 and abs(smoothed[0] - 100) <= DEADBAND


def test_smoothing_settles_on_a_steady_target():
    times = [k * 0.5 for k in range(40)]
    smoothed = smooth_positions(times, [0.0] + [300.0] * 39, window_width=WINDOW_W)

    assert abs(smoothed[-1] - 300.0) <= DEADBAND + 1e-6


def test_smoothing_keeps_a_moving_target_centred_to_the_end():
    """Zero phase: a steady pan is followed with only the dead zone's offset,
    the last sample included (no end-of-clip lag)."""
    times = [k * 0.5 for k in range(20)]
    xs = [40.0 + 20 * k for k in range(20)]  # 40 px/s, well under the cap

    smoothed = smooth_positions(times, xs, window_width=WINDOW_W, max_x=MAX_X)

    assert all(abs(s - x) <= DEADBAND + 1.0 for s, x in zip(smoothed, xs))


def test_smoothed_positions_stay_in_the_pan_range():
    times = [k * 0.5 for k in range(6)]

    smoothed = smooth_positions(
        times, [0.0, 0.0, 0.0, 438.0, 438.0, 438.0], window_width=WINDOW_W, max_x=MAX_X
    )

    assert all(0.0 <= s <= MAX_X for s in smoothed)


# ── keyframe decimation ───────────────────────────────────────────────────────


def test_a_flat_track_needs_only_its_endpoints():
    track = [(k * 0.5, 219.0) for k in range(120)]

    assert decimate(track) == [(0.0, 219.0), (59.5, 219.0)]


def test_decimation_caps_the_keyframes_and_keeps_both_endpoints():
    rng = np.random.default_rng(7)
    track = [(k * 0.5, float(x)) for k, x in enumerate(rng.uniform(0, MAX_X, 300))]

    out = decimate(track)

    assert len(out) == MAX_CROP_KEYFRAMES
    assert out[0] == track[0] and out[-1] == track[-1]
    assert [t for t, _ in out] == sorted(t for t, _ in out)
    assert set(out) <= set(track)


def test_decimation_keeps_the_turning_point():
    down = [(k * 0.5, 400.0 - 10 * k) for k in range(31)]  # 400 → 100
    up = [(15.5 + k * 0.5, 110.0 + 10 * k) for k in range(30)]

    out = decimate(down + up, max_keyframes=3)

    assert out == [(0.0, 400.0), (15.0, 100.0), (30.0, 400.0)]


def test_short_tracks_pass_through():
    assert decimate([(0.0, 1.0)]) == [(0.0, 1.0)]
    assert decimate([(0.0, 1.0), (1.0, 50.0), (2.0, 7.0)]) == [
        (0.0, 1.0),
        (1.0, 50.0),
        (2.0, 7.0),
    ]


# ── planning ──────────────────────────────────────────────────────────────────


def test_plan_follows_the_face_within_the_pan_range():
    obs = [_obs(k * 0.5, _face(150 + 10 * k)) for k in range(30)]

    plan = plan_crop_track(obs, SRC)

    assert plan.track is not None
    assert plan.samples == plan.face_samples == 30
    times = [t for t, _ in plan.track]
    xs = [x for _, x in plan.track]
    assert times[0] == 0.0 and times[-1] == pytest.approx(14.5)
    assert all(0 <= x <= MAX_X for x in xs)
    assert xs == sorted(xs) and xs[-1] > xs[0] + 100  # panned right with the face


def test_plan_holds_the_last_face_through_a_gap():
    obs = [_obs(0.0, _face(500)), _obs(0.5), _obs(1.0), _obs(1.5)]

    plan = plan_crop_track(obs, SRC)

    assert plan.track == [(0.0, x_left_for(500, *SRC[:1], WINDOW_W)), (1.5, 399.0)]


def test_plan_starts_on_the_first_face_when_the_clip_opens_without_one():
    obs = [_obs(0.0), _obs(0.5), _obs(1.0, _face(500))]

    plan = plan_crop_track(obs, SRC)

    assert plan.track == [(0.0, 399.0), (1.0, 399.0)]


def test_plan_caps_keyframes_for_a_long_clip():
    rng = np.random.default_rng(3)
    obs = [_obs(k * 0.5, _face(float(rng.uniform(0, 640)))) for k in range(600)]

    plan = plan_crop_track(obs, SRC)

    assert 2 <= len(plan.track) <= MAX_CROP_KEYFRAMES
    assert plan.track[0][0] == 0.0 and plan.track[-1][0] == pytest.approx(299.5)


def test_no_face_means_letterbox():
    plan = plan_crop_track([_obs(0.0), _obs(0.5)], SRC)

    assert plan.track is None and plan.reason == "no face found"


def test_split_screen_means_letterbox():
    obs = [_obs(k * 0.5, _face(100), _face(540)) for k in range(10)]

    plan = plan_crop_track(obs, SRC)

    assert plan.track is None and plan.reason == "split screen"


def test_several_similar_faces_mean_letterbox():
    obs = [_obs(k * 0.5, _face(250), _face(390, h=55)) for k in range(10)]

    plan = plan_crop_track(obs, SRC)

    assert plan.track is None and plan.reason == "several faces of similar size"


def test_a_small_second_face_does_not_stop_tracking():
    obs = [_obs(k * 0.5, _face(250, h=80), _face(390, h=40)) for k in range(10)]

    assert plan_crop_track(obs, SRC).track is not None


def test_a_source_without_pan_room_is_letterboxed():
    obs = [_obs(0.0, _face(100, cy=300))]

    plan = plan_crop_track(obs, (360, 640))

    assert plan.track is None and plan.reason == "no horizontal pan room"


# ── frame sampling (real decode of synthetic sources) ─────────────────────────


@pytest.fixture(scope="module")
def moving_block(tmp_path_factory) -> Path:
    """72 frames at 24 fps; the block's left edge is 4 px per frame index."""
    path = tmp_path_factory.mktemp("reframe") / "moving.mp4"
    return write_block_video(path, block_left=lambda i: 4 * i)


def _block_left(image: np.ndarray) -> int:
    xs = np.nonzero((image >= 200).all(axis=2).any(axis=0))[0]
    return int(xs.min())


def test_samples_are_timed_from_the_clip_start(moving_block):
    samples = list(reframe_service.sample_frames(moving_block, 1.0, 2.5, fps=2.0))

    # every 0.5 s, plus the window's last frame (source frame 59)
    assert [round(s.t, 6) for s in samples] == [0.0, 0.5, 1.0, round(35 / 24, 6)]
    # frame index = 24 * (start + t): the block is where that frame drew it
    assert [_block_left(s.image) for s in samples] == [96, 144, 192, 236]
    assert all(s.display_size == SRC for s in samples)


def test_the_last_frame_is_sampled_once(moving_block):
    """A window whose last frame is a regular sample does not repeat it."""
    samples = list(reframe_service.sample_frames(moving_block, 0.0, 1.0 + 1 / 48))

    assert [round(s.t, 6) for s in samples] == [0.0, 0.5, 1.0]


def test_large_sources_are_fitted_into_the_detector_input(tmp_path):
    src = write_block_video(
        tmp_path / "hd.mp4", block_left=lambda i: 600, size=(1280, 720), frames=6
    )

    sample = next(reframe_service.sample_frames(src, 0.0, 0.2, fps=2.0))

    assert sample.image.shape == (360, 640, 3)
    assert sample.display_size == (1280, 720)
    assert _block_left(sample.image) == 300


def test_rotated_sources_are_sampled_as_displayed(moving_block, tmp_path):
    rotated = tmp_path / "rot90.mp4"
    ffmpeg_tools.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-display_rotation", "90", "-i", str(moving_block), "-c", "copy",
            str(rotated),
        ]
    )  # fmt: skip
    t = 12 / 24

    sample = next(reframe_service.sample_frames(rotated, t, t + 0.1, fps=2.0))

    want = np.asarray(ffmpeg_tools.grab_frame(rotated, t))[:, :, ::-1]  # BGR
    assert sample.display_size == (360, 640)
    assert sample.image.shape == want.shape == (640, 360, 3)
    assert np.abs(sample.image.astype(int) - want.astype(int)).mean() < 2.0


def test_sampling_stops_when_cancelled(moving_block):
    cancel = threading.Event()
    frames = reframe_service.sample_frames(
        moving_block, 0.0, 3.0, fps=8.0, cancel=cancel
    )
    next(frames)
    cancel.set()

    with pytest.raises(reframe_service.ReframeCancelled):
        next(frames)


# ── get_crop_track: decode + detect + plan ────────────────────────────────────


def test_crop_track_follows_the_block(moving_block):
    detector = BlockDetector()

    plan = reframe_service.get_crop_track(moving_block, 0.5, 2.5, detector=detector)

    assert detector.calls == plan.samples == 5  # t = 0, .5, 1, 1.5 + last frame
    assert plan.track is not None
    times = [t for t, _ in plan.track]
    xs = [x for _, x in plan.track]
    assert times[0] == 0.0 and times[-1] == pytest.approx(47 / 24)  # clip time
    assert xs == sorted(xs) and xs[-1] > xs[0]
    # block centre at clip t is 4 * 24 * (0.5 + t) + BLOCK / 2 (source frame
    # 12 + 24 t): inside the window at the first and the last keyframe
    for t, x in ((times[0], xs[0]), (times[-1], xs[-1])):
        centre = 4 * round(24 * (0.5 + t)) + BLOCK / 2
        assert x + BLOCK / 2 <= centre <= x + WINDOW_W - BLOCK / 2


def test_faces_are_mapped_back_to_source_pixels(tmp_path):
    """A detection in the 640 px detector input is a source-pixel x in the
    track: a 1280 px source doubles it."""
    src = write_block_video(
        tmp_path / "hd.mp4", block_left=lambda i: 0, size=(1280, 720), frames=12
    )
    # Face centred at x = 500 in the 640x360 input → 1000 in the source.
    detector = ScriptedDetector([[Face(x=470.0, y=150.0, w=60.0, h=60.0, score=0.9)]])

    plan = reframe_service.get_crop_track(src, 0.0, 0.4, detector=detector)

    window_w = 404  # pan_crop of 1280x720
    assert plan.track is not None and plan.track[0][0] == 0.0
    assert {x for _t, x in plan.track} == {1000.0 - window_w / 2}
