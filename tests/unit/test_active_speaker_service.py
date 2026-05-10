"""Unit tests for active_speaker_service (W2.2)."""
from __future__ import annotations

from app.services.active_speaker_service import (
    FaceObservation,
    detect_split_screen,
    smooth_cues,
)


def test_smooth_cues_passes_through_high_conf():
    obs = [
        FaceObservation(0.0, 0.5, 0.5, 0.9),
        FaceObservation(0.1, 0.6, 0.5, 0.9),
    ]
    cues = smooth_cues(obs, window=3)
    assert len(cues) == 2
    assert cues[0].layout == "fullscreen"


def test_smooth_cues_drops_low_conf():
    obs = [
        FaceObservation(0.0, 0.5, 0.5, 0.1),  # below default 0.3
        FaceObservation(0.1, 0.6, 0.5, 0.9),
    ]
    cues = smooth_cues(obs)
    assert len(cues) == 1
    assert cues[0].cx == 0.6


def test_smooth_cues_centre_of_mass():
    obs = [
        FaceObservation(timestamp=t, cx=t, cy=0.5, confidence=0.9)
        for t in (0.0, 0.1, 0.2, 0.3, 0.4)
    ]
    cues = smooth_cues(obs, window=5)
    # Middle frame averages five 0..0.4 -> 0.2.
    middle = cues[2]
    assert abs(middle.cx - 0.2) < 1e-6


def test_smooth_cues_empty():
    assert smooth_cues([]) == []


def test_detect_split_screen_two_far_faces():
    fr = [
        [FaceObservation(0, 0.1, 0.5, 0.9), FaceObservation(0, 0.9, 0.5, 0.9)]
        for _ in range(10)
    ]
    assert detect_split_screen(fr) is True


def test_detect_split_screen_single_face():
    fr = [[FaceObservation(0, 0.5, 0.5, 0.9)] for _ in range(10)]
    assert detect_split_screen(fr) is False
