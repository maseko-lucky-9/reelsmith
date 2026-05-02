"""Unit tests for reframe service."""
from __future__ import annotations

from app.services.reframe_service import (
    LetterboxReframe,
    StubReframe,
    apply_crop_track,
    get_reframe_service,
)
from app.settings import settings


def test_letterbox_returns_empty_track():
    reframe = LetterboxReframe()
    assert reframe.get_crop_track("dummy.mp4") == []


def test_stub_returns_static_track():
    reframe = StubReframe()
    track = reframe.get_crop_track("dummy.mp4")
    assert len(track) >= 1
    for t, cx, cy in track:
        assert 0.0 <= cx <= 1.0
        assert 0.0 <= cy <= 1.0


def test_get_reframe_service_letterbox(monkeypatch):
    monkeypatch.setattr(settings, "reframe_provider", "letterbox")
    svc = get_reframe_service()
    assert isinstance(svc, LetterboxReframe)


def test_get_reframe_service_stub(monkeypatch):
    monkeypatch.setattr(settings, "reframe_provider", "stub")
    svc = get_reframe_service()
    assert isinstance(svc, StubReframe)


def test_apply_crop_track_empty_uses_centre():
    import numpy as np

    frame = np.zeros((720, 1280, 3), dtype="uint8")
    cropped = apply_crop_track(frame, [], 0.0, 405, 720)
    assert cropped.shape == (720, 405, 3)


def test_apply_crop_track_nearest_point():
    import numpy as np

    frame = np.zeros((720, 1280, 3), dtype="uint8")
    track = [(0.0, 0.3, 0.5), (1.0, 0.7, 0.5)]
    cropped = apply_crop_track(frame, track, 0.1, 405, 720)
    assert cropped.shape == (720, 405, 3)


def test_ema_smoothing_converges():
    """Stub reframe always returns centre; EMA on a FaceTrackReframe is internal."""
    reframe = StubReframe()
    track = reframe.get_crop_track("x.mp4")
    # All points should be stable (static centre)
    cxs = [p[1] for p in track]
    variance = sum((c - 0.5) ** 2 for c in cxs) / len(cxs)
    assert variance < 0.01
