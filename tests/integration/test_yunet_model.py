"""The real YuNet model: lazy download, SHA-256 pin, onnxruntime inference.

Downloads ``face_detection_yunet_2023mar.onnx`` (232,589 bytes, MIT, from the
opencv_zoo repository at a pinned commit) into a temp dir, so it needs the
network. The default run never downloads it: unit tests use a fake fetch and
a stub session.

Run with: pytest -m integration tests/integration/test_yunet_model.py
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from app.services import face_detector

pytestmark = pytest.mark.integration


def _no_network(url: str, dest: Path) -> None:
    raise AssertionError(f"cached model was downloaded again from {url}")


def test_real_model_downloads_verifies_and_runs(tmp_path):
    path = face_detector.ensure_model(tmp_path)

    data = path.read_bytes()
    assert len(data) == face_detector.YUNET_SIZE == 232_589
    assert hashlib.sha256(data).hexdigest() == face_detector.YUNET_SHA256
    assert face_detector.ensure_model(tmp_path, fetch=_no_network) == path

    detector = face_detector.YuNetDetector(path)
    assert detector.detect(np.zeros((360, 640, 3), np.uint8)) == []
    noise = np.random.default_rng(0).integers(0, 256, (640, 480, 3), dtype=np.uint8)
    assert all(0.0 <= f.score <= 1.0 for f in detector.detect(noise))
