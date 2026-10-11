"""Face-tracked reframe: a crop track that keeps the speaker in the 9:16 reel.

``get_crop_track`` decodes ``[start, end)`` of the source with PyAV at
``SAMPLE_FPS``, fits each sampled frame (as displayed: rotation applied) into
the detector's 640x640 input, runs a ``FaceDetector`` and turns the faces into
``render_service`` crop keyframes ``(seconds from start, crop left edge in
source px)``:

1. **Faces.** A box counts when it is at least ``MIN_FACE_HEIGHT`` of the
   frame tall and scores ``MIN_FACE_SCORE``, or, in a wide shot, at least
   ``SMALL_FACE_HEIGHT`` tall and scores the stricter ``SMALL_FACE_SCORE``
   (small boxes are less reliable). A frame's main face is the largest;
   faces within 10 % of its area go to the one nearest the frame centre.
2. **Fallbacks** (no track: the reel is letterboxed as before): no usable face
   in the whole clip, a usable face in fewer than ``MIN_FACE_SHARE`` of the
   samples (slides, credits), a split screen (``active_speaker_service
   .detect_split_screen``), several faces of similar size in most face frames
   (``EQUAL_FACE_AREA``), or a source with no horizontal pan room. The split
   screen and similar-size tests count only faces of ``MIN_FACE_HEIGHT``.
3. **Primary face** (``follow_primary``). The crop follows one subject: in
   each frame, the face that continues the previous primary
   (``same_subject``: within ``SAME_FACE_RADIUS`` window widths horizontally,
   at least ``SAME_FACE_SIZE`` of its height; the frame's main face first).
   Another face takes over only once it has been the frame's main face for
   ``SWITCH_SECONDS`` (``SMALL_SWITCH_FACTOR`` times that if it was only ever
   a small face), and then from its first sample: a shorter cutaway seen on
   its own never moves the crop (cutaways split by faceless frames can add
   up, see ADR-006 *Known limits*), and a real change of speaker adds no lag.
4. **Position.** The window (``render_service.pan_crop``) is centred on the
   primary face and clamped to ``[0, src_w - window]``. A frame without it
   holds the last position; frames before its first sample take that one.
5. **Smoothing.** A zero-phase EMA (``EMA_ALPHA`` per sample, forward then
   backward: no lag), then a dead zone of ``DEADBAND`` window widths (the
   crop stays put while the speaker sways) and a cap of ``MAX_SPEED`` window
   widths per second, so the crop never snaps across the frame.
6. **Keyframes.** At most ``render_service.MAX_CROP_KEYFRAMES``: the greedy
   top-down simplification keeps both endpoints and adds the point that
   deviates most from the piecewise-linear track until the cap is reached or
   every point is within half a pixel.

``face_track`` is the orchestrator's entry point: it gets the detector from
``face_detector.get_face_detector()`` (downloading the model on first use)
and stops decoding when the ``ffmpeg_tools.to_thread_cancellable`` cancel
event is set.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np

from app.services import clip_service, face_detector, ffmpeg_tools, render_service
from app.services.active_speaker_service import FaceObservation, detect_split_screen
from app.services.face_detector import Face, FaceDetector

log = logging.getLogger(__name__)

SAMPLE_FPS = 2.0
MIN_FACE_SCORE = 0.6
MIN_FACE_HEIGHT = 0.05  # of the frame height
# Wide shots: a face down to 2.5 % of the frame height (9 px of the 640x360
# detector input of a 16:9 source; YuNet reported none under 2 % in the 1,644
# samples of the two G2 talks) counts from 0.8: above spike S1's one false
# positive (0.71, the back of a head), below the 0.86 that the host crossing
# the G2 Wikimania stage reached at 7.0 s. Audience and crowd faces that small
# reach 0.86 and 0.89 in those talks, so the score cannot tell them from a
# speaker; continuity does (``follow_primary``).
SMALL_FACE_HEIGHT = 0.025  # of the frame height
SMALL_FACE_SCORE = 0.8
# Continuity: a face this close to the primary is the primary. Half a window
# per 0.5 s sample keeps a speaker moving up to one window width per second
# (twice MAX_SPEED); the size test separates a cut to someone else at a nearby
# spot (the G2 Wikimania cutaway face: 0.39 windows away, 0.35 of the
# speaker's height).
SAME_FACE_RADIUS = 0.5  # window widths, horizontal
SAME_FACE_SIZE = 0.5  # smaller / larger face height
# Another face takes over once it has been the frame's main face this long
# (4 samples at 2 fps, so a face must be on screen 1.5 to 2.0 s depending on
# where its shot falls between samples). A 1 s cutaway spans at most about
# 1.03 s of samples, under the 1.375 s this needs with _TIME_SLACK, so it never
# does; the switch is back-dated to the new face's first sample, so the wait
# adds no lag. A stint unseen for longer than this ends. A face only ever seen
# small must last twice as long while there is a primary: the small faces of
# the G2 talks were mostly audience and crowd.
SWITCH_SECONDS = 1.5
SMALL_SWITCH_FACTOR = 2.0
# Fewer samples than this share with a usable face (slides, credits, shots the
# detector cannot read) → letterbox: the crop would be placed blind for most
# of the reel.
MIN_FACE_SHARE = 0.5
# Sample times are frame times: at 2 fps they fall at about 0.02 and 0.50 s
# offsets, so a 4-sample span is 1.48 or 1.52 s. Durations get a quarter of a
# sample interval of slack.
_TIME_SLACK = 0.25 / SAMPLE_FPS  # 0.125 s
_NEAR_EQUAL_AREA = 0.9  # primary-face tie: prefer the most central
EQUAL_FACE_AREA = 0.6  # second face this large → "several faces" frame
CROWDED_SHARE = 0.5  # of the frames with a face
EMA_ALPHA = 0.5
_EMA_PAD = 6  # samples of reflection padding (0.5**6 < 2 %)
MAX_SPEED = 0.5  # window widths per second
DEADBAND = 0.1  # window widths
_SIMPLIFY_TOLERANCE_PX = 0.5


class ReframeCancelled(RuntimeError):
    """Decoding stopped because the cancel event was set."""


@dataclass(frozen=True)
class FrameSample:
    t: float  # seconds from the clip start
    image: np.ndarray  # BGR, displayed orientation, fitted into the detector input
    display_size: tuple[int, int]  # source (width, height) as displayed


@dataclass(frozen=True)
class Observation:
    t: float  # seconds from the clip start
    faces: tuple[Face, ...]  # in SOURCE pixels (displayed orientation)


@dataclass(frozen=True)
class CropTrackPlan:
    """A crop track for ``render_clip``, or ``None`` with the reason why not."""

    track: list[render_service.CropKeyframe] | None
    reason: str
    samples: int
    face_samples: int


# ── faces ─────────────────────────────────────────────────────────────────────


def usable_faces(faces: Sequence[Face], src_h: int) -> list[Face]:
    """Faces worth following: at least ``MIN_FACE_HEIGHT`` tall scoring
    ``MIN_FACE_SCORE``, or smaller (a wide shot) scoring ``SMALL_FACE_SCORE``."""
    min_h, small_h = MIN_FACE_HEIGHT * src_h, SMALL_FACE_HEIGHT * src_h
    return [
        f
        for f in faces
        if (f.h >= min_h and f.score >= MIN_FACE_SCORE)
        or (f.h >= small_h and f.score >= SMALL_FACE_SCORE)
    ]


def primary_face(faces: Sequence[Face], src_w: int) -> Face | None:
    """The frame's main face: the largest, or the most central of the faces
    within 10 % of its area."""
    if not faces:
        return None
    largest = max(f.area for f in faces)
    near = [f for f in faces if f.area >= _NEAR_EQUAL_AREA * largest]
    return min(near, key=lambda f: abs(f.cx - src_w / 2))


def same_subject(a: Face, b: Face, radius: float) -> bool:
    """Whether ``b`` can be ``a`` in a later sample: at most ``radius`` px
    apart horizontally (the crop only pans) and of a similar height (a cut to
    someone else usually changes it)."""
    small, large = sorted((a.h, b.h))
    return abs(a.cx - b.cx) <= radius and small >= SAME_FACE_SIZE * large


@dataclass(frozen=True)
class _Sighting:
    index: int  # into the observations
    t: float
    face: Face


def follow_primary(
    observations: Sequence[Observation], src_w: int, window_w: float, *, src_h: int
) -> list[Face | None]:
    """The primary face of each sample; ``None`` where it is not seen.

    * The primary continues as the frame's main face (``primary_face``) when
      that is a ``same_subject`` face (within ``SAME_FACE_RADIUS`` window
      widths), otherwise as the ``same_subject`` face nearest it, even when
      another face is larger.
    * Any other main face starts a stint. A stint that lasts
      ``SWITCH_SECONDS`` makes its face the primary, back-dated to the
      stint's first sample; a stint seen only as faces under
      ``MIN_FACE_HEIGHT`` of ``src_h`` needs ``SMALL_SWITCH_FACTOR`` times
      that while there is a primary.
    * A stint ends when another face becomes the main face, when the primary
      is the main face again, or when its face goes unseen for longer than
      ``SWITCH_SECONDS`` (samples without any face do not end it). Its reach
      grows with the time since its last sighting (one radius per sample
      interval), so a moving face detected only now and then stays one stint.
    * The clip opens without a primary, so an opening cutaway is a stint
      like any other. If no stint lasts long enough in the whole clip, the
      last one is the primary (a clip too short to tell).
    """
    radius = SAME_FACE_RADIUS * window_w
    min_h = MIN_FACE_HEIGHT * src_h
    chosen: list[Face | None] = [None] * len(observations)
    primary: Face | None = None
    stint: list[_Sighting] = []
    for i, o in enumerate(observations):
        if not o.faces:
            continue
        main = primary_face(o.faces, src_w)
        assert main is not None
        if primary is not None:
            previous = primary
            near = [f for f in o.faces if same_subject(previous, f, radius)]
            if near:
                primary = (
                    main if main in near else min(near, key=lambda f: abs(f.cx - previous.cx))
                )
                chosen[i] = primary
            if main in near:
                stint = []
                continue
        gap = o.t - stint[-1].t if stint else 0.0
        if not (
            stint
            and gap <= SWITCH_SECONDS + _TIME_SLACK
            and same_subject(stint[-1].face, main, radius * max(1.0, gap * SAMPLE_FPS))
        ):
            stint = []
        stint.append(_Sighting(i, o.t, main))
        need = SWITCH_SECONDS
        if primary is not None and all(s.face.h < min_h for s in stint):
            need *= SMALL_SWITCH_FACTOR
        if o.t - stint[0].t >= need - _TIME_SLACK:
            for s in stint:
                chosen[s.index] = s.face
            primary, stint = main, []
    if primary is None:
        for s in stint:
            chosen[s.index] = s.face
    return chosen


def _several_similar(faces: Sequence[Face]) -> bool:
    if len(faces) < 2:
        return False
    first, second = sorted((f.area for f in faces), reverse=True)[:2]
    return second >= EQUAL_FACE_AREA * first


def _split_screen(
    observations: Sequence[Observation], src_size: tuple[int, int]
) -> bool:
    src_w, src_h = src_size
    return detect_split_screen(
        [
            [FaceObservation(o.t, f.cx / src_w, f.cy / src_h, f.score) for f in o.faces]
            for o in observations
        ]
    )


# ── positions ─────────────────────────────────────────────────────────────────


def x_left_for(face_cx: float, src_w: int, window_w: int) -> float:
    """Left edge of a ``window_w`` window centred on ``face_cx``, clamped."""
    return min(max(face_cx - window_w / 2, 0.0), float(src_w - window_w))


def hold_gaps(xs: Sequence[float | None]) -> list[float] | None:
    """Fill ``None`` with the last known value (the first one before it)."""
    known = [x for x in xs if x is not None]
    if not known:
        return None
    last = known[0]
    filled: list[float] = []
    for x in xs:
        if x is not None:
            last = x
        filled.append(last)
    return filled


def _zero_phase_ema(xs: Sequence[float], alpha: float) -> list[float]:
    """EMA forward, then backward over the result, on a copy of ``xs`` padded
    at both ends by odd reflection (as ``scipy.signal.filtfilt`` does): a
    linear stretch stays linear, so neither end lags."""
    n = len(xs)
    pad = min(n - 1, _EMA_PAD)
    head = [2 * xs[0] - xs[i] for i in range(pad, 0, -1)]
    tail = [2 * xs[-1] - xs[n - 1 - i] for i in range(1, pad + 1)]
    forward = [float(x) for x in (*head, *xs, *tail)]
    for k in range(1, len(forward)):
        forward[k] = forward[k - 1] + alpha * (forward[k] - forward[k - 1])
    for k in range(len(forward) - 2, -1, -1):
        forward[k] = forward[k + 1] + alpha * (forward[k] - forward[k + 1])
    return forward[pad : pad + n]


def smooth_positions(
    times: Sequence[float],
    xs: Sequence[float],
    *,
    window_width: float,
    max_x: float | None = None,
    alpha: float = EMA_ALPHA,
    max_speed: float = MAX_SPEED,
    deadband: float = DEADBAND,
) -> list[float]:
    """Crop positions for per-sample targets ``xs`` (see the module docstring).

    1. Zero-phase EMA: the whole clip is known, so smoothing adds no lag
       behind a moving speaker. Clamped to ``[0, max_x]`` when given.
    2. Dead zone: the crop moves only by how far the target strays beyond
       ``deadband`` window widths from it, so swaying never moves it.
    3. Speed cap: at most ``max_speed`` window widths per second.
    """
    if not xs:
        return []
    target = _zero_phase_ema(xs, alpha)
    if max_x is not None:
        target = [min(max(x, 0.0), max_x) for x in target]
    band = deadband * window_width
    speed = max_speed * window_width
    out = [target[0]]
    for k in range(1, len(target)):
        delta = target[k] - out[-1]
        move = math.copysign(max(abs(delta) - band, 0.0), delta)
        limit = speed * max(times[k] - times[k - 1], 0.0)
        out.append(out[-1] + min(max(move, -limit), limit))
    return out


def decimate(
    track: Sequence[render_service.CropKeyframe],
    max_keyframes: int = render_service.MAX_CROP_KEYFRAMES,
    tolerance: float = _SIMPLIFY_TOLERANCE_PX,
) -> list[render_service.CropKeyframe]:
    """At most ``max_keyframes`` of ``track``'s points, endpoints included,
    chosen greedily by largest deviation from the current piecewise-linear
    approximation; points within ``tolerance`` px are never added."""
    if max_keyframes < 2:
        raise ValueError("max_keyframes must be at least 2")
    points = list(track)
    if len(points) <= 2:
        return points
    keep = {0, len(points) - 1}
    while len(keep) < max_keyframes:
        kept = sorted(keep)
        worst, worst_err = None, tolerance
        for a, b in zip(kept, kept[1:]):
            (ta, xa), (tb, xb) = points[a], points[b]
            for i in range(a + 1, b):
                ti, xi = points[i]
                line = xa if tb == ta else xa + (xb - xa) * (ti - ta) / (tb - ta)
                if abs(xi - line) > worst_err:
                    worst, worst_err = i, abs(xi - line)
        if worst is None:
            break
        keep.add(worst)
    return [points[i] for i in sorted(keep)]


# ── planning ──────────────────────────────────────────────────────────────────


def plan_crop_track(
    observations: Sequence[Observation],
    src_size: tuple[int, int],
    target_aspect_ratio: float = 9 / 16,
) -> CropTrackPlan:
    """The crop track for faces observed over a clip (pure; no I/O)."""
    src_w, src_h = src_size
    window = render_service.pan_crop(
        clip_service.reel_geometry(src_w, src_h, target_aspect_ratio)
    )
    samples = len(observations)
    if window.width >= src_w:
        return CropTrackPlan(None, "no horizontal pan room", samples, 0)
    usable = [
        Observation(o.t, tuple(usable_faces(o.faces, src_h))) for o in observations
    ]
    with_faces = [o for o in usable if o.faces]
    if not with_faces:
        return CropTrackPlan(None, "no face found", samples, 0)
    if len(with_faces) < MIN_FACE_SHARE * samples:
        return CropTrackPlan(
            None,
            f"a face in only {len(with_faces)}/{samples} samples",
            samples,
            len(with_faces),
        )
    # The layout tests count only large faces, as before small faces were
    # usable: a poster or an audience face must not letterbox a tracked clip.
    min_h = MIN_FACE_HEIGHT * src_h
    large = [Observation(o.t, tuple(f for f in o.faces if f.h >= min_h)) for o in usable]
    if _split_screen(large, src_size):
        return CropTrackPlan(None, "split screen", samples, len(with_faces))
    with_large = [o for o in large if o.faces]
    crowded = sum(1 for o in with_large if _several_similar(o.faces))
    if crowded > CROWDED_SHARE * len(with_large):
        return CropTrackPlan(
            None, "several faces of similar size", samples, len(with_faces)
        )
    targets = [
        None if face is None else x_left_for(face.cx, src_w, window.width)
        for face in follow_primary(usable, src_w, window.width, src_h=src_h)
    ]
    xs = hold_gaps(targets)
    assert xs is not None  # with_faces is not empty, so some sample has a primary
    times = [o.t for o in usable]
    smoothed = smooth_positions(
        times, xs, window_width=window.width, max_x=float(src_w - window.width)
    )
    track = decimate(list(zip(times, smoothed)))
    return CropTrackPlan(
        track,
        f"{len(with_faces)}/{samples} samples with a face",
        samples,
        len(with_faces),
    )


# ── decoding ──────────────────────────────────────────────────────────────────


def sample_frames(
    video_path: str | Path,
    start: float,
    end: float,
    *,
    fps: float = SAMPLE_FPS,
    max_side: int = face_detector.INPUT_SIDE,
    cancel: threading.Event | None = None,
) -> Iterator[FrameSample]:
    """Frames of ``[start, end)`` about every ``1 / fps`` seconds.

    Times follow ffmpeg's ``-ss`` (the render's clock): ``t`` is the frame's
    presentation time minus the container start time minus ``start``, so
    ``t = 0`` is the reel's first frame. Each sample is the first frame at or
    after its target time, plus the window's last frame. Images are BGR, rotated as players show them and
    scaled down (never up) to fit ``max_side`` x ``max_side``. Raises
    ``ReframeCancelled`` as soon as ``cancel`` is set.
    """
    if end <= start:
        raise ValueError(f"invalid sampling window start={start} end={end}")
    duration = end - start
    step = 1.0 / fps
    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        origin = (
            container.start_time / av.time_base
            if container.start_time is not None
            else 0.0
        )
        container.seek(
            int((origin + start) / stream.time_base), stream=stream, backward=True
        )
        next_t = 0.0
        unsampled: tuple[av.VideoFrame, float] | None = None  # last frame seen
        for frame in container.decode(stream):
            if cancel is not None and cancel.is_set():
                raise ReframeCancelled("face tracking cancelled")
            if frame.time is None:
                continue
            t = frame.time - origin - start
            if t >= duration - 1e-9:
                break
            if t < -1e-6:
                continue
            if t < next_t - 1e-6:
                unsampled = (frame, t)
                continue
            unsampled = None
            while next_t <= t + 1e-6:
                next_t += step
            yield _fitted(frame, max(t, 0.0), max_side)
        if unsampled is not None:
            # The window's last frame, so the track reaches the clip's end
            # instead of holding a position up to 1 / fps seconds old.
            yield _fitted(unsampled[0], unsampled[1], max_side)


def _fitted(frame: av.VideoFrame, t: float, max_side: int) -> FrameSample:
    rotation = int(round(getattr(frame, "rotation", 0) or 0)) % 360
    coded_w, coded_h = frame.width, frame.height
    disp_w, disp_h = (coded_h, coded_w) if rotation in (90, 270) else (coded_w, coded_h)
    scale = min(1.0, max_side / disp_w, max_side / disp_h)
    width = max(1, min(max_side, round(coded_w * scale)))
    height = max(1, min(max_side, round(coded_h * scale)))
    image = frame.reformat(width=width, height=height, format="bgr24").to_ndarray()
    if rotation:
        # counter-clockwise, as ffmpeg_tools._displayed (verified vs the CLI)
        image = np.rot90(image, k=rotation // 90)
    return FrameSample(
        t=t, image=np.ascontiguousarray(image), display_size=(disp_w, disp_h)
    )


def observe(
    samples: Iterator[FrameSample], detector: FaceDetector
) -> tuple[list[Observation], tuple[int, int] | None]:
    """Detect faces in each sample and map them back to source pixels."""
    observations: list[Observation] = []
    size: tuple[int, int] | None = None
    for sample in samples:
        size = sample.display_size
        sx = sample.image.shape[1] / size[0]
        sy = sample.image.shape[0] / size[1]
        faces = tuple(
            Face(x=f.x / sx, y=f.y / sy, w=f.w / sx, h=f.h / sy, score=f.score)
            for f in detector.detect(sample.image)
        )
        observations.append(Observation(t=sample.t, faces=faces))
    return observations, size


def get_crop_track(
    video_path: str | Path,
    start: float,
    end: float,
    *,
    detector: FaceDetector,
    fps: float = SAMPLE_FPS,
    target_aspect_ratio: float = 9 / 16,
    cancel: threading.Event | None = None,
) -> CropTrackPlan:
    """Decode, detect and plan the crop track for ``[start, end)``."""
    samples = sample_frames(video_path, start, end, fps=fps, cancel=cancel)
    observations, size = observe(samples, detector)
    if size is None:
        return CropTrackPlan(None, "no frames in the clip window", 0, 0)
    return plan_crop_track(observations, size, target_aspect_ratio)


def face_track(
    video_path: str, start: float, end: float, target_aspect_ratio: float = 9 / 16
) -> CropTrackPlan:
    """``get_crop_track`` with the configured detector (blocking; run it in a
    worker thread via ``ffmpeg_tools.to_thread_cancellable``).

    The source is probed first, so a source that cannot be panned (or read)
    never triggers the model download.
    """
    t0 = time.perf_counter()
    src_w, src_h = ffmpeg_tools.video_size(video_path)
    window = render_service.pan_crop(
        clip_service.reel_geometry(src_w, src_h, target_aspect_ratio)
    )
    if window.width >= src_w:
        return CropTrackPlan(None, "no horizontal pan room", 0, 0)
    plan = get_crop_track(
        video_path,
        start,
        end,
        detector=face_detector.get_face_detector(),
        target_aspect_ratio=target_aspect_ratio,
        cancel=ffmpeg_tools.current_cancel_event(),
    )
    log.info(
        "Face track %s [%.2f, %.2f]: %s, %d keyframes (%.2fs)",
        video_path, start, end, plan.reason,
        len(plan.track) if plan.track else 0, time.perf_counter() - t0,
    )  # fmt: skip
    return plan
