"""Reframe service — letterbox (current) or face-tracked vertical crop."""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)

# Centre track: list of (time_seconds, cx_normalised, cy_normalised)
CentreTrack = list[tuple[float, float, float]]


@runtime_checkable
class ReframeProtocol(Protocol):
    def get_crop_track(self, clip_path: str) -> CentreTrack: ...


class LetterboxReframe:
    """No-op reframe — render service uses its existing letterbox logic."""

    def get_crop_track(self, clip_path: str) -> CentreTrack:
        return []


class StubReframe:
    """Returns a static centre track for tests."""

    def get_crop_track(self, clip_path: str) -> CentreTrack:
        return [(0.0, 0.5, 0.5), (1.0, 0.5, 0.5)]


class FaceTrackReframe:
    """MediaPipe face detection + EMA-smoothed crop track."""

    _EMA_ALPHA = 0.3
    _SAMPLE_EVERY_N = 10
    _MAX_GAP_SECONDS = 2.0

    def get_crop_track(self, clip_path: str) -> CentreTrack:
        try:
            import cv2  # type: ignore[import]
            import mediapipe as mp  # type: ignore[import]
        except ImportError as e:
            log.warning("mediapipe/cv2 not available (%s); falling back to centre", e)
            return []

        detector = mp.solutions.face_detection.FaceDetection(  # type: ignore[attr-defined]
            model_selection=0, min_detection_confidence=0.5
        )

        cap = cv2.VideoCapture(clip_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        track: CentreTrack = []
        last_good: tuple[float, float] | None = None
        last_good_t: float | None = None
        frame_idx = 0

        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break

                if frame_idx % self._SAMPLE_EVERY_N == 0:
                    t = frame_idx / fps
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    result = detector.process(rgb)
                    h, w = frame.shape[:2]

                    cx_raw, cy_raw = None, None
                    if result.detections:
                        # Pick the largest face box
                        best = max(
                            result.detections,
                            key=lambda d: (
                                d.location_data.relative_bounding_box.width
                                * d.location_data.relative_bounding_box.height
                            ),
                        )
                        bb = best.location_data.relative_bounding_box
                        cx_raw = bb.xmin + bb.width / 2
                        cy_raw = bb.ymin + bb.height / 2

                    if cx_raw is not None:
                        if last_good is None:
                            cx_smooth = cx_raw
                            cy_smooth = cy_raw
                        else:
                            prev_cx, prev_cy = last_good
                            cx_smooth = self._EMA_ALPHA * cx_raw + (1 - self._EMA_ALPHA) * prev_cx
                            cy_smooth = self._EMA_ALPHA * cy_raw + (1 - self._EMA_ALPHA) * prev_cy
                        last_good = (cx_smooth, cy_smooth)
                        last_good_t = t
                        track.append((t, cx_smooth, cy_smooth))
                    elif last_good is not None:
                        gap = t - (last_good_t or t)
                        if gap <= self._MAX_GAP_SECONDS:
                            track.append((t, *last_good))
                        # Beyond max_gap: no entry — render service will use centre fallback

                frame_idx += 1
        finally:
            cap.release()
            detector.close()

        return track


def get_reframe_service() -> ReframeProtocol:
    from app.settings import settings

    if settings.reframe_provider == "face_track":
        try:
            import mediapipe  # noqa: F401  # type: ignore[import]
            return FaceTrackReframe()
        except ImportError:
            log.warning("mediapipe not available; falling back to letterbox")
            return LetterboxReframe()

    if settings.reframe_provider == "stub":
        return StubReframe()

    return LetterboxReframe()


def apply_crop_track(frame, track: CentreTrack, t: float, target_w: int, target_h: int):
    """Crop a numpy frame to target_w×target_h centred on the tracked position at time t."""
    import numpy as np  # type: ignore[import]

    h, w = frame.shape[:2]
    if not track:
        cx_n, cy_n = 0.5, 0.5
    else:
        # Find nearest track point
        best = min(track, key=lambda p: abs(p[0] - t))
        cx_n, cy_n = best[1], best[2]

    cx = int(cx_n * w)
    cy = int(cy_n * h)

    half_w = target_w // 2
    half_h = target_h // 2

    x0 = max(0, min(cx - half_w, w - target_w))
    y0 = max(0, min(cy - half_h, h - target_h))
    x1 = x0 + target_w
    y1 = y0 + target_h

    return frame[y0:y1, x0:x1]
