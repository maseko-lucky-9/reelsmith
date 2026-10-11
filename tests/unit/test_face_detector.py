"""YuNet face detector on onnxruntime: decoding, padding, model cache (offline).

The real model is downloaded only by ``tests/integration/test_yunet_model.py``;
here the network fetch is a fake and the ONNX session a stub.
"""

from __future__ import annotations

import hashlib
import math
import os
import subprocess
import sys
from dataclasses import astuple
from pathlib import Path

import numpy as np
import pytest

from app.services import face_detector

SIDE = face_detector.INPUT_SIDE
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _blank_outputs() -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for stride in (8, 16, 32):
        n = (SIDE // stride) ** 2
        out[f"cls_{stride}"] = np.zeros((1, n, 1), np.float32)
        out[f"obj_{stride}"] = np.zeros((1, n, 1), np.float32)
        out[f"bbox_{stride}"] = np.zeros((1, n, 4), np.float32)
        out[f"kps_{stride}"] = np.zeros((1, n, 10), np.float32)
    return out


def _put(out, stride, row, col, *, cls, obj, bbox) -> None:
    idx = row * (SIDE // stride) + col
    out[f"cls_{stride}"][0, idx, 0] = cls
    out[f"obj_{stride}"][0, idx, 0] = obj
    out[f"bbox_{stride}"][0, idx] = bbox


# ── decode_yunet ──────────────────────────────────────────────────────────────


def test_decode_one_anchor_exactly():
    out = _blank_outputs()
    # stride 16, row 5, col 7: centre ((7 + .25) * 16, (5 + .5) * 16) = (116, 88),
    # size (2 * 16, 3 * 16) = (32, 48); score sqrt(.81 * 1) = .9
    _put(out, 16, 5, 7, cls=0.81, obj=1.0, bbox=[0.25, 0.5, math.log(2), math.log(3)])

    [face] = face_detector.decode_yunet(out)

    assert astuple(face) == pytest.approx((100.0, 64.0, 32.0, 48.0, 0.9))
    assert (face.cx, face.cy, face.area) == pytest.approx((116.0, 88.0, 32.0 * 48.0))


def test_decode_clamps_scores_and_applies_the_threshold():
    out = _blank_outputs()
    _put(out, 8, 1, 1, cls=1.7, obj=0.64, bbox=[0, 0, 0, 0])  # clamp → sqrt(.64)
    _put(out, 32, 2, 2, cls=0.5, obj=0.5, bbox=[0, 0, 0, 0])  # .5 < .6: dropped

    faces = face_detector.decode_yunet(out, score_threshold=0.6)

    assert [round(f.score, 6) for f in faces] == [0.8]


def test_decode_suppresses_overlapping_boxes_keeping_the_best():
    out = _blank_outputs()
    # Same face seen at strides 8 and 16 (IoU ~0.6), a second face far away.
    _put(out, 8, 10, 10, cls=0.9, obj=0.9, bbox=[0, 0, math.log(4), math.log(4)])
    _put(out, 16, 5, 5, cls=0.8, obj=0.8, bbox=[0.25, 0.25, math.log(2), math.log(2)])
    _put(out, 32, 15, 15, cls=0.7, obj=0.7, bbox=[0, 0, 0, 0])

    faces = face_detector.decode_yunet(out, nms_threshold=0.3)

    assert [round(f.score, 6) for f in faces] == [0.9, 0.7]


def test_nms_keeps_disjoint_boxes_and_orders_by_score():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 10, 10], [50, 50, 10, 10]], np.float32)
    scores = np.array([0.7, 0.9, 0.8], np.float32)

    assert face_detector.nms(boxes, scores, 0.3) == [1, 2]


# ── input blob ────────────────────────────────────────────────────────────────


def test_input_blob_is_bgr_planes_zero_padded_bottom_right():
    bgr = np.zeros((360, 640, 3), np.uint8)
    bgr[..., 0], bgr[..., 1], bgr[..., 2] = 10, 20, 30

    blob = face_detector.to_input_blob(bgr)

    assert blob.shape == (1, 3, SIDE, SIDE) and blob.dtype == np.float32
    assert (blob[0, 0, :360, :640] == 10).all()
    assert (blob[0, 2, :360, :640] == 30).all()
    assert not blob[0, :, 360:, :].any()


def test_input_blob_rejects_an_image_larger_than_the_model_input():
    with pytest.raises(ValueError, match="640"):
        face_detector.to_input_blob(np.zeros((360, 641, 3), np.uint8))


class _FakeSession:
    def __init__(self, outputs):
        self.outputs = outputs
        self.inputs: list[np.ndarray] = []

    def get_outputs(self):
        return [type("O", (), {"name": name})() for name in self.outputs]

    def run(self, _names, feed):
        self.inputs.append(feed["input"])
        return list(self.outputs.values())


def test_detector_runs_the_session_on_the_padded_blob():
    out = _blank_outputs()
    _put(out, 16, 5, 7, cls=0.81, obj=1.0, bbox=[0.25, 0.5, math.log(2), math.log(3)])
    session = _FakeSession(out)
    detector = face_detector.YuNetDetector(session=session)

    faces = detector.detect(np.full((200, 300, 3), 7, np.uint8))

    assert [astuple(f) for f in faces] == [pytest.approx((100.0, 64.0, 32.0, 48.0, 0.9))]
    [blob] = session.inputs
    assert blob.shape == (1, 3, SIDE, SIDE) and blob[0, :, :200, :300].min() == 7


# ── model cache ───────────────────────────────────────────────────────────────

_FAKE_MODEL = b"fake-onnx-model-bytes"


@pytest.fixture
def pinned_fake_model(monkeypatch):
    """Pin the module to the fake model's digest (the real one is 232 KB)."""
    monkeypatch.setattr(
        face_detector, "YUNET_SHA256", hashlib.sha256(_FAKE_MODEL).hexdigest()
    )


def _fetcher(payload: bytes, calls: list[str]):
    def fetch(url: str, dest: Path) -> None:
        calls.append(url)
        dest.write_bytes(payload)

    return fetch


def test_model_is_downloaded_once_and_verified(tmp_path, pinned_fake_model):
    calls: list[str] = []
    fetch = _fetcher(_FAKE_MODEL, calls)

    first = face_detector.ensure_model(tmp_path / "models", fetch=fetch)
    second = face_detector.ensure_model(tmp_path / "models", fetch=fetch)

    assert first == second == tmp_path / "models" / face_detector.YUNET_FILENAME
    assert first.read_bytes() == _FAKE_MODEL
    assert calls == [face_detector.YUNET_URL]
    assert sorted(p.name for p in first.parent.iterdir()) == [first.name]


def test_a_download_with_the_wrong_digest_is_rejected(tmp_path, pinned_fake_model):
    calls: list[str] = []

    with pytest.raises(face_detector.ModelIntegrityError, match="SHA-256"):
        face_detector.ensure_model(tmp_path, fetch=_fetcher(b"tampered", calls))

    assert list(tmp_path.iterdir()) == []  # no model, no partial file


def test_a_corrupt_cached_model_is_downloaded_again(tmp_path, pinned_fake_model):
    (tmp_path / face_detector.YUNET_FILENAME).write_bytes(b"truncated")
    calls: list[str] = []

    path = face_detector.ensure_model(tmp_path, fetch=_fetcher(_FAKE_MODEL, calls))

    assert path.read_bytes() == _FAKE_MODEL
    assert len(calls) == 1


def test_the_pinned_model_url_is_an_immutable_commit():
    """``main`` could change the bytes behind the URL; the digest would then
    reject every download. A 40-hex commit pins them."""
    commit = face_detector.YUNET_URL.split("/raw/")[1].split("/")[0]
    assert len(commit) == 40 and int(commit, 16) >= 0
    assert len(face_detector.YUNET_SHA256) == 64


def test_get_face_detector_uses_the_settings_model_dir_and_caches(
    tmp_path, monkeypatch
):
    seen: list[Path] = []

    def fake_ensure(model_dir, **_kwargs):
        seen.append(Path(model_dir))
        return Path(model_dir) / "m.onnx"

    built: list[Path] = []

    class _Det:
        def __init__(self, model_path=None, **_kwargs):
            built.append(model_path)

    monkeypatch.setattr(face_detector, "ensure_model", fake_ensure)
    monkeypatch.setattr(face_detector, "YuNetDetector", _Det)
    monkeypatch.setattr(face_detector.settings, "reframe_model_dir", str(tmp_path))
    face_detector.reset_for_tests()
    try:
        first = face_detector.get_face_detector()
        second = face_detector.get_face_detector()
    finally:
        face_detector.reset_for_tests()

    assert first is second
    assert seen == [tmp_path]
    assert built == [tmp_path / "m.onnx"]


def test_importing_the_pipeline_downloads_nothing(tmp_path):
    """The model is fetched on first face-tracked render, never at import."""
    model_dir = tmp_path / "models"
    env = {
        **os.environ,
        "YTVIDEO_REFRAME_MODEL_DIR": str(model_dir),
        "YTVIDEO_REFRAME_PROVIDER": "face_track",
    }
    code = (
        "import app.main, app.workers.orchestrator, app.services.reframe_service, "
        "app.services.face_detector"
    )
    subprocess.run(
        [sys.executable, "-c", code], cwd=_REPO_ROOT, env=env, check=True, timeout=120
    )

    assert not model_dir.exists()
