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

from app.services import clip_service, ffmpeg_tools, reframe_service, render_service
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
    small_h = reframe_service.SMALL_FACE_HEIGHT * SRC[1]  # 9 px of 360
    sure = reframe_service.SMALL_FACE_SCORE
    keep = [
        _face(100, h=min_h),
        _face(300, score=reframe_service.MIN_FACE_SCORE),
        _face(500, h=small_h, score=sure),  # small, but a clear face
        _face(550, h=min_h - 1, score=sure),
    ]
    drop = [
        _face(400, score=0.59),
        _face(200, h=min_h - 1, score=0.79),  # small and unsure
        _face(250, h=8.9, score=0.99),  # under the 2.5 % floor (9 px of 360)
    ]

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
    obs = [_obs(0.0, _face(500)), _obs(0.5, _face(500)), _obs(1.0), _obs(1.5)]

    plan = plan_crop_track(obs, SRC)

    assert plan.track == [(0.0, x_left_for(500, *SRC[:1], WINDOW_W)), (1.5, 399.0)]


def test_plan_starts_on_the_first_face_when_the_clip_opens_without_one():
    obs = [_obs(0.0), _obs(0.5), _obs(1.0, _face(500)), _obs(1.5, _face(500))]

    plan = plan_crop_track(obs, SRC)

    assert plan.track == [(0.0, 399.0), (1.5, 399.0)]


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


# ── T041: cutaways, wide shots, slides ────────────────────────────────────────


def _x_at(plan, t: float) -> float:
    """The crop's left edge at clip time ``t`` (the render interpolates)."""
    return float(np.interp(t, [k for k, _ in plan.track], [x for _, x in plan.track]))


def _steady(face: Face, start: float, count: int) -> list[Observation]:
    return [_obs(start + k * 0.5, face) for k in range(count)]


SPEAKER_X = x_left_for(150, SRC[0], WINDOW_W)  # 49
OTHER_X = x_left_for(500, SRC[0], WINDOW_W)  # 399


def test_a_one_second_cutaway_does_not_move_the_crop():
    """The speaker is gone and an audience face fills the frame for 1 s (its
    first and last samples 1.0 s apart): the crop stays on the speaker."""
    speaker, audience = _face(150), _face(500, h=40)
    obs = [
        *_steady(speaker, 0.0, 10),  # 0 - 4.5 s
        *_steady(audience, 5.0, 3),  # 5.0, 5.5, 6.0
        *_steady(speaker, 6.5, 10),  # back to the speaker
    ]

    plan = plan_crop_track(obs, SRC)

    assert plan.track is not None
    assert {x for _t, x in plan.track} == {SPEAKER_X}


def test_a_bigger_face_for_a_second_does_not_take_the_crop_from_the_speaker():
    """Someone walks past the camera: a larger face for 1 s while the speaker
    is still in the frame. The crop keeps following the speaker."""
    speaker, passer_by = _face(150), _face(500, h=120)
    obs = [
        *_steady(speaker, 0.0, 10),
        *[_obs(5.0 + k * 0.5, speaker, passer_by) for k in range(3)],
        *_steady(speaker, 6.5, 10),
    ]

    plan = plan_crop_track(obs, SRC)

    assert {x for _t, x in plan.track} == {SPEAKER_X}


def test_a_cutaway_face_near_the_speakers_spot_is_not_the_speaker():
    """The G2 Wikimania cutaway, scaled: the audience face is 0.39 window
    widths from where the speaker was and 0.35 of their height. Close enough
    to pass for the speaker by position alone; the size tells them apart."""
    speaker, audience = _face(150, h=60), _face(150 + 0.39 * WINDOW_W, h=21)
    obs = [
        *_steady(speaker, 0.0, 10),
        *_steady(audience, 5.0, 3),
        *_steady(speaker, 6.5, 10),
    ]

    plan = plan_crop_track(obs, SRC)

    assert {x for _t, x in plan.track} == {SPEAKER_X}


def test_a_walking_speaker_stays_the_subject():
    """The speaker crosses the frame at 60 px per sample (0.3 window widths,
    faster than the crop may pan) and a larger face shows for 1 s mid-walk:
    one subject throughout, so the speaker never leaves the window."""
    walk = [_face(100 + 60 * k) for k in range(1, 8)]  # 160 → 520, 2.0 - 5.0 s
    passer_by = _face(600, h=120)
    obs = [
        *_steady(_face(100), 0.0, 4),
        *[
            _obs(2 + k * 0.5, f, *([passer_by] if 2 <= k <= 4 else []))
            for k, f in enumerate(walk)
        ],
        *_steady(walk[-1], 5.5, 10),
    ]

    plan = plan_crop_track(obs, SRC)

    for o in obs:
        speaker_cx = o.faces[0].cx
        assert _x_at(plan, o.t) <= speaker_cx <= _x_at(plan, o.t) + WINDOW_W, o.t


def test_a_face_held_for_switch_seconds_takes_over():
    """The boundary: a new face that is the frame's main face for exactly
    1.5 s (4 samples) is a change of speaker, so the crop heads its way,
    even though the first speaker comes back afterwards."""
    assert reframe_service.SWITCH_SECONDS == 1.5
    obs = [
        *_steady(_face(150), 0.0, 10),
        *_steady(_face(500), 5.0, 4),  # 5.0, 5.5, 6.0, 6.5
        *_steady(_face(150), 7.0, 10),
    ]

    plan = plan_crop_track(obs, SRC)

    assert max(x for _t, x in plan.track) > SPEAKER_X + DEADBAND


def test_a_persisting_new_speaker_takes_the_crop_from_the_start_of_their_shot():
    """A cut to another speaker who stays on screen: the crop moves to them,
    and the switch is back-dated to their first sample (the clip is known in
    full), so only the speed cap delays it: 2 s after the cut it has covered
    90 % of the move. Applied only once confirmed (``SWITCH_SECONDS`` later),
    it would still be mid-pan."""
    obs = [*_steady(_face(150), 0.0, 10), *_steady(_face(500), 5.0, 20)]

    plan = plan_crop_track(obs, SRC)

    assert abs(plan.track[-1][1] - OTHER_X) <= DEADBAND + 1.0  # settled on them
    assert _x_at(plan, 7.0) >= SPEAKER_X + 0.9 * (OTHER_X - SPEAKER_X)


def test_two_short_cutaways_do_not_add_up_to_a_switch():
    """The same audience face for 1 s, slides (no face) for 3 s, then the
    same face for 1 s again: neither stint lasts ``SWITCH_SECONDS``, so the
    crop never leaves the speaker."""
    speaker, audience = _face(150), _face(500, h=40)
    obs = [
        *_steady(speaker, 0.0, 10),
        *_steady(audience, 5.0, 3),
        *[_obs(6.5 + k * 0.5) for k in range(6)],  # 6.5 - 9.0 s: slides
        *_steady(audience, 9.5, 3),
        *_steady(speaker, 11.0, 10),
    ]

    plan = plan_crop_track(obs, SRC)

    assert {x for _t, x in plan.track} == {SPEAKER_X}


def test_a_clip_opening_on_a_cutaway_starts_on_the_speaker():
    """A cutaway in the first second is not taken for the subject: the
    lead-in takes the speaker's position."""
    obs = [*_steady(_face(500, h=40), 0.0, 2), *_steady(_face(150), 1.0, 20)]

    plan = plan_crop_track(obs, SRC)

    assert {x for _t, x in plan.track} == {SPEAKER_X}


def test_a_small_speaker_in_a_wide_shot_is_tracked_not_held():
    """A cut from a close-up to a wide shot where the speaker's face is 12 px
    of 360 (3.3 %, under ``MIN_FACE_HEIGHT``) and walks right, scoring 0.86
    (the host crossing the G2 Wikimania stage scored that): the crop follows
    them instead of holding the close-up's position."""
    close_up = _face(150, h=60)
    wide = [_face(300 + 10 * k, h=12, score=0.86) for k in range(20)]  # 300 → 490
    obs = [
        *_steady(close_up, 0.0, 10),
        *[_obs(5.0 + k * 0.5, f) for k, f in enumerate(wide)],
    ]

    plan = plan_crop_track(obs, SRC)

    assert plan.track is not None and plan.face_samples == 30
    end_x = x_left_for(490, SRC[0], WINDOW_W)
    assert abs(plan.track[-1][1] - end_x) <= DEADBAND + 1.0


def test_an_unsure_small_face_is_not_followed():
    """The guard: a small face scoring 0.79, under ``SMALL_FACE_SCORE`` (as
    most faces that small did on the G2 Wikimania talk: median 0.70, mostly
    audience), does not count, so the crop holds."""
    unsure = 0.79
    obs = [
        *_steady(_face(150, h=60), 0.0, 20),
        *[_obs(10 + k * 0.5, _face(300 + 10 * k, h=12, score=unsure)) for k in range(10)],
    ]

    plan = plan_crop_track(obs, SRC)

    assert plan.face_samples == 20
    assert {x for _t, x in plan.track} == {SPEAKER_X}


def _clip_with_faces(faces: int, samples: int) -> list[Observation]:
    """A talking head for the first ``faces`` samples, then slides."""
    return [_obs(k * 0.5, *([_face(150)] if k < faces else [])) for k in range(samples)]


@pytest.mark.parametrize(
    ("faces", "samples", "tracked"),
    [(10, 20, True), (9, 20, False), (11, 21, True), (10, 21, False)],
)
def test_a_clip_mostly_without_a_face_is_letterboxed(faces, samples, tracked):
    """Slides or credits for most of the clip: with a face in fewer than
    ``MIN_FACE_SHARE`` of the samples there is no track (the letterbox, as
    for a split screen). Exactly at the share, the clip is still tracked."""
    assert reframe_service.MIN_FACE_SHARE == 0.5  # the boundary cases above

    plan = plan_crop_track(_clip_with_faces(faces, samples), SRC)

    assert (plan.track is not None) is tracked
    assert plan.face_samples == faces
    if not tracked:
        assert plan.reason == f"a face in only {faces}/{samples} samples"


def test_a_mostly_slides_clip_gets_no_track_from_the_detector(tmp_path):
    """Through ``get_crop_track``: a fake detector that finds a face in one
    sample of four (the talking head between slides)."""
    src = write_block_video(tmp_path / "slides.mp4", block_left=lambda i: 0, frames=96)
    face = Face(x=470.0, y=150.0, w=60.0, h=60.0, score=0.9)
    detector = ScriptedDetector([[face], [], [], []])

    plan = reframe_service.get_crop_track(src, 0.0, 4.0, detector=detector)

    assert detector.calls == plan.samples == 9  # 2 fps over 4 s, plus the last frame
    assert plan.track is None
    assert plan.reason == f"a face in only 3/{plan.samples} samples"


# ── review of PR #67: scenarios on a 1920x1080 source ─────────────────────────

HD = (1920, 1080)
HD_WINDOW = 608  # pan_crop of a 1920x1080 source on the 9:16 canvas
HD_DEADBAND = reframe_service.DEADBAND * HD_WINDOW


def _hd(cx: float, *, h: float = 150, score: float = 0.9, cy: float = 500) -> Face:
    return Face(x=cx - h / 2, y=cy - h / 2, w=h, h=h, score=score)


def _hd_x(cx: float) -> float:
    return x_left_for(cx, HD[0], HD_WINDOW)


def _largest_face_track(obs: list[Observation], src: tuple[int, int]):
    """The track without continuity (main before T041): every sample's own
    main face. With one face in the frame, continuity must not change it."""
    src_w, src_h = src
    window_w = render_service.pan_crop(clip_service.reel_geometry(src_w, src_h)).width
    targets = []
    for o in obs:
        face = primary_face(usable_faces(o.faces, src_h), src_w)
        targets.append(None if face is None else x_left_for(face.cx, src_w, window_w))
    times = [o.t for o in obs]
    xs = smooth_positions(
        times, hold_gaps(targets), window_width=window_w, max_x=float(src_w - window_w)
    )
    return decimate(list(zip(times, xs)))


def test_the_speaker_walking_past_a_smaller_face_keeps_the_crop():
    """Review S11: the speaker walks 900 → 1250 px past a static face of 60 %
    of his height (too small for "several similar faces") at 1000 px. Both
    are within the continuity radius; the frame's main face, the speaker,
    stays the primary, so the crop ends on him, not stuck near the static
    face (635 px before the fix, against 885 px without continuity)."""
    static = _hd(1000, h=90)
    obs = [
        *[_obs(k * 0.5, _hd(900), static) for k in range(6)],
        *[_obs(3 + k * 0.5, _hd(1010 + 40 * k), static) for k in range(7)],
        *[_obs(6.5 + k * 0.5, _hd(1250), static) for k in range(20)],
    ]

    plan = plan_crop_track(obs, HD)

    assert abs(plan.track[-1][1] - _hd_x(1250)) <= HD_DEADBAND + 1.0


def _walker_seen_every_other_sample() -> list[Observation]:
    """Review S3c: 200 px per sample from t = 0, detected every 2nd sample."""
    return [
        _obs(k * 0.5, _hd(min(200 + 200 * k, 1700))) if k % 2 == 0 else _obs(k * 0.5)
        for k in range(30)
    ]


def _speaker_stands_then_walks_seen_every_other_sample() -> list[Observation]:
    """Review S3d: stands 5 s, then walks 200 px per sample, detected every
    2nd sample (400 px between sightings: more than the radius), stops."""
    obs = [_obs(k * 0.5, _hd(200)) for k in range(10)]
    for k in range(1, 21):
        cx = min(200 + 200 * k, 1700)
        obs.append(_obs(4.5 + k * 0.5, _hd(cx)) if k % 2 == 0 else _obs(4.5 + k * 0.5))
    return [*obs, *[_obs(15.0 + k * 0.5, _hd(1700)) for k in range(4)]]


@pytest.mark.parametrize(
    "scenario",
    [_walker_seen_every_other_sample, _speaker_stands_then_walks_seen_every_other_sample],
    ids=["S3c", "S3d"],
)
def test_a_walker_seen_every_other_sample_is_followed(scenario):
    """A lone face moving 400 px between sightings 1 s apart: a stint's
    reach grows with the time since its last sighting, so the walker is one
    subject and the track is exactly the one without continuity."""
    obs = scenario()

    plan = plan_crop_track(obs, HD)

    assert plan.track == _largest_face_track(obs, HD)


def test_a_small_face_does_not_make_a_split_screen():
    """Review S10, first half: a 3.2 % poster face (score 0.85) on the far
    side of the frame in every sample. Small faces are not counted by the
    split-screen test, so the clip is tracked on the speaker."""
    obs = [_obs(k * 0.5, _hd(700), _hd(1650, h=35, score=0.85, cy=200)) for k in range(40)]

    plan = plan_crop_track(obs, HD)

    assert plan.track is not None, plan.reason
    assert {x for _t, x in plan.track} == {_hd_x(700)}


def test_a_small_face_does_not_make_the_clip_crowded():
    """A 4.8 % audience face (score 0.85) beside a 5.6 % speaker would be a
    "similar" second face (area ratio 0.75); small faces are not counted by
    that test either, so the clip is tracked on the speaker."""
    obs = [_obs(k * 0.5, _hd(900, h=60), _hd(1100, h=52, score=0.85)) for k in range(40)]

    plan = plan_crop_track(obs, HD)

    assert plan.track is not None, plan.reason
    assert {x for _t, x in plan.track} == {_hd_x(900)}


def test_an_opening_wide_shot_keeps_its_small_speaker():
    """The 2 x wait applies only once there is a primary: a clip that opens
    on a wide shot (the speaker's face 3.3 %, score 0.86, for 2 s) takes that
    speaker after ``SWITCH_SECONDS`` like any face, so the reel opens on them
    before a cut to someone else."""
    obs = [
        *_steady(_face(150, h=12, score=0.86), 0.0, 5),  # 0 - 2.0 s
        *_steady(_face(500), 2.5, 20),
    ]

    plan = plan_crop_track(obs, SRC)

    assert plan.track[0][0] == 0.0 and abs(plan.track[0][1] - SPEAKER_X) <= DEADBAND


@pytest.mark.parametrize(("missing", "moves"), [(6, False), (7, True)])
def test_a_small_face_needs_twice_as_long_to_take_the_crop(missing, moves):
    """Review S10b: the speaker turns to the slides (undetected) while a 3.2 %
    audience face (score 0.86, 500 px away: no split screen) stays in view.
    A competitor made only of small faces needs 2 x ``SWITCH_SECONDS`` while
    there is a primary: 2.5 s (6 samples) holds the crop, 3.0 s (7) moves it."""
    assert reframe_service.SWITCH_SECONDS == 1.5
    audience = _hd(1250, h=35, score=0.86, cy=900)
    obs = [
        _obs(k * 0.5, *([] if 10 <= k < 10 + missing else [_hd(750)]), audience)
        for k in range(40)
    ]

    plan = plan_crop_track(obs, HD)

    assert (max(x for _t, x in plan.track) > _hd_x(750) + HD_DEADBAND) is moves


def test_a_competitor_seen_large_once_waits_only_switch_seconds():
    """The 2 x wait is for faces only ever seen small. A new speaker on the
    edge of the size floor (5.6 % and 4.4 % of the frame height in turns)
    for 2 s takes the crop after ``SWITCH_SECONDS``."""
    edge = [_face(500, h=20), _face(500, h=16, score=0.86)]  # 18 px is the floor
    obs = [
        *_steady(_face(150), 0.0, 10),
        *[_obs(5.0 + k * 0.5, edge[k % 2]) for k in range(5)],  # 5.0 - 7.0 s
        *_steady(_face(150), 7.5, 10),
    ]

    plan = plan_crop_track(obs, SRC)

    assert max(x for _t, x in plan.track) > SPEAKER_X + DEADBAND


def test_the_speaker_coming_back_between_two_cutaways_resets_the_competitor():
    """Review R4: cutaway 1 s, the speaker for one sample, the same cutaway
    1 s again. The speaker's return ends the cutaway's stint, so the two
    halves do not add up to ``SWITCH_SECONDS``."""
    speaker, audience = _face(150), _face(500, h=40)
    obs = [
        *_steady(speaker, 0.0, 10),
        *_steady(audience, 5.0, 2),
        _obs(6.0, speaker),
        *_steady(audience, 6.5, 2),
        *_steady(speaker, 7.5, 10),
    ]

    plan = plan_crop_track(obs, SRC)

    assert {x for _t, x in plan.track} == {SPEAKER_X}


@pytest.mark.parametrize(("gap", "moves"), [(1.5, True), (2.0, False)])
def test_a_stint_survives_a_faceless_gap_up_to_switch_seconds(gap, moves):
    """Review R6: the gap is measured from the stint's last sighting. A
    cutaway seen at 5.0 and 5.5 s, then no face at all, then again ``gap``
    later: unseen for 1.5 s it is the same stint (2.0 s long: it takes
    over); unseen for 2.0 s ("longer than SWITCH_SECONDS") it starts over."""
    speaker, audience = _face(150), _face(500, h=40)
    back = 5.5 + gap
    obs = [
        *_steady(speaker, 0.0, 10),
        *_steady(audience, 5.0, 2),
        *[_obs(6.0 + k * 0.5) for k in range(int(round((back - 6.0) / 0.5)))],
        _obs(back, audience),
        *_steady(speaker, back + 0.5, 10),
    ]

    plan = plan_crop_track(obs, SRC)

    assert (max(x for _t, x in plan.track) > SPEAKER_X + DEADBAND) is moves


@pytest.mark.parametrize(("shot", "moves"), [(3, False), (4, True)])
def test_switch_seconds_allows_for_frame_time_jitter(shot, moves):
    """Sample times are frame times: at 2 fps they fall at 0.02 and 0.50 s
    offsets, so a 4-sample shot spans 1.48 s, not 1.5 s. It still takes
    over; a 3-sample (1 s) cutaway still does not."""
    times = [k * 0.5 + (0.02 if k % 2 == 0 else 0.0) for k in range(30)]
    obs = [
        _obs(t, _face(500) if 10 <= k < 10 + shot else _face(150))
        for k, t in enumerate(times)
    ]

    plan = plan_crop_track(obs, SRC)

    assert (max(x for _t, x in plan.track) > SPEAKER_X + DEADBAND) is moves


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
