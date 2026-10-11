"""Face detection for the face-tracked reframe: YuNet on onnxruntime.

``FaceDetector`` is the seam: ``detect(bgr)`` takes an ``H x W x 3`` uint8 BGR
image no larger than ``INPUT_SIDE`` on either side and returns faces in that
image's pixels. ``YuNetDetector`` is the real one:

* **Model.** ``face_detection_yunet_2023mar.onnx`` (232,589 bytes, MIT, Shiqi
  Yu) from the opencv_zoo repository at a pinned commit. It is NOT shipped:
  ``ensure_model`` downloads it on first use into ``settings.reframe_model_dir``
  and accepts it only if its SHA-256 matches ``YUNET_SHA256`` (the digest in
  the repository's Git LFS pointer). Nothing downloads at import time.
* **Runtime.** onnxruntime (already a dependency via faster-whisper), CPU
  provider. The graph has a fixed 1x3x640x640 input: the frame is placed
  top-left in a zeroed 640x640 tensor, raw 0..255 BGR, which is what OpenCV's
  ``FaceDetectorYN`` feeds it (it pads bottom/right with zeros).
* **Decoding** (``decode_yunet``) mirrors OpenCV's ``FaceDetectorYN``: for
  strides 8/16/32 the score is ``sqrt(clamp(cls) * clamp(obj))``, the box
  centre is ``(col + dx, row + dy) * stride`` and its size
  ``exp(dw|dh) * stride``; then greedy NMS. On 120 frames of two real talks
  it matched ``cv2.FaceDetectorYN`` to 0.02 px (spike S1, PR body).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from app.settings import settings

log = logging.getLogger(__name__)

INPUT_SIDE = 640
_STRIDES = (8, 16, 32)

YUNET_FILENAME = "face_detection_yunet_2023mar.onnx"
YUNET_URL = (
    "https://github.com/opencv/opencv_zoo/raw/"
    "f12e12798e8314f7c074a6656816c048dcc95b7a/"
    "models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
YUNET_SIZE = 232_589
_DOWNLOAD_TIMEOUT_SECONDS = 60.0
_MAX_DOWNLOAD_BYTES = 4 * YUNET_SIZE


@dataclass(frozen=True)
class Face:
    """A face box in pixels of the image it was found in, with its score."""

    x: float
    y: float
    w: float
    h: float
    score: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return self.w * self.h


class FaceDetector(Protocol):
    def detect(self, bgr: np.ndarray) -> list[Face]:
        """Faces in ``bgr`` (H x W x 3 uint8, both sides <= ``INPUT_SIDE``)."""
        ...


class ModelIntegrityError(RuntimeError):
    """The downloaded model is not the pinned file."""


# ── pure parts ────────────────────────────────────────────────────────────────


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Greedy non-maximum suppression over ``[x, y, w, h]`` boxes.

    Returns the kept indices, best score first; a box is dropped when its IoU
    with an already kept box exceeds ``iou_threshold``.
    """
    order = np.argsort(-scores, kind="stable")
    keep: list[int] = []
    while order.size:
        best, rest = int(order[0]), order[1:]
        keep.append(best)
        x1 = np.maximum(boxes[best, 0], boxes[rest, 0])
        y1 = np.maximum(boxes[best, 1], boxes[rest, 1])
        x2 = np.minimum(
            boxes[best, 0] + boxes[best, 2], boxes[rest, 0] + boxes[rest, 2]
        )
        y2 = np.minimum(
            boxes[best, 1] + boxes[best, 3], boxes[rest, 1] + boxes[rest, 3]
        )
        inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
        union = (
            boxes[best, 2] * boxes[best, 3] + boxes[rest, 2] * boxes[rest, 3] - inter
        )
        iou = inter / np.maximum(union, 1e-9)
        order = rest[iou <= iou_threshold]
    return keep


def decode_yunet(
    outputs: Mapping[str, np.ndarray],
    *,
    score_threshold: float = 0.6,
    nms_threshold: float = 0.3,
    side: int = INPUT_SIDE,
) -> list[Face]:
    """Faces from YuNet's raw ``cls_*``/``obj_*``/``bbox_*`` outputs, in input
    pixels, best score first (see the module docstring for the formulas)."""
    boxes: list[np.ndarray] = []
    scores: list[np.ndarray] = []
    for stride in _STRIDES:
        cols = side // stride
        cls = np.clip(outputs[f"cls_{stride}"][0, :, 0], 0.0, 1.0)
        obj = np.clip(outputs[f"obj_{stride}"][0, :, 0], 0.0, 1.0)
        score = np.sqrt(cls * obj)
        idx = np.nonzero(score >= score_threshold)[0]
        if not idx.size:
            continue
        delta = outputs[f"bbox_{stride}"][0, idx].astype(np.float64)
        row, col = idx // cols, idx % cols
        cx = (col + delta[:, 0]) * stride
        cy = (row + delta[:, 1]) * stride
        w = np.exp(delta[:, 2]) * stride
        h = np.exp(delta[:, 3]) * stride
        boxes.append(np.stack([cx - w / 2, cy - h / 2, w, h], axis=1))
        scores.append(score[idx].astype(np.float64))
    if not boxes:
        return []
    all_boxes, all_scores = np.concatenate(boxes), np.concatenate(scores)
    return [
        Face(*(float(v) for v in all_boxes[i]), score=float(all_scores[i]))
        for i in nms(all_boxes, all_scores, nms_threshold)
    ]


def to_input_blob(bgr: np.ndarray, side: int = INPUT_SIDE) -> np.ndarray:
    """``bgr`` as a 1x3xSIDExSIDE float32 tensor, zero-padded bottom/right."""
    h, w = bgr.shape[:2]
    if bgr.ndim != 3 or bgr.shape[2] != 3:
        raise ValueError(f"expected an H x W x 3 BGR image, got shape {bgr.shape}")
    if h > side or w > side:
        raise ValueError(f"image {w}x{h} does not fit the {side}x{side} model input")
    blob = np.zeros((1, 3, side, side), np.float32)
    blob[0, :, :h, :w] = bgr.transpose(2, 0, 1)
    return blob


# ── model file ────────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _download(url: str, dest: Path) -> None:
    """Stream ``url`` into ``dest`` (redirects followed, size capped)."""
    import httpx

    with httpx.stream(
        "GET", url, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT_SECONDS
    ) as response:
        response.raise_for_status()
        received = 0
        with dest.open("wb") as fh:
            for chunk in response.iter_bytes():
                received += len(chunk)
                if received > _MAX_DOWNLOAD_BYTES:
                    raise ModelIntegrityError(
                        f"{url} is larger than {_MAX_DOWNLOAD_BYTES} bytes"
                    )
                fh.write(chunk)


def ensure_model(
    model_dir: str | Path, *, fetch: Callable[[str, Path], None] = _download
) -> Path:
    """Path of the verified YuNet model in ``model_dir``, downloading it once.

    A cached file is re-hashed on every call (232 KB) and downloaded again if
    it does not match. A download is written to a hidden temp file beside the
    model and moved into place only when its SHA-256 is ``YUNET_SHA256``;
    otherwise ``ModelIntegrityError`` is raised and nothing is left behind.
    """
    path = Path(model_dir) / YUNET_FILENAME
    if path.is_file():
        if _sha256(path) == YUNET_SHA256:
            return path
        log.warning("Cached face model %s fails its SHA-256; downloading again", path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        log.info("Downloading face model %s -> %s", YUNET_URL, path)
        fetch(YUNET_URL, partial)
        digest = _sha256(partial)
        if digest != YUNET_SHA256:
            raise ModelIntegrityError(
                f"face model from {YUNET_URL} has SHA-256 {digest}, "
                f"expected {YUNET_SHA256}"
            )
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    return path


# ── detector ──────────────────────────────────────────────────────────────────


class YuNetDetector:
    """YuNet 2023mar through onnxruntime (CPU). Thread-safe: onnxruntime
    sessions may be run concurrently."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        score_threshold: float = 0.6,
        nms_threshold: float = 0.3,
        threads: int = 2,
        session=None,
    ) -> None:
        if session is None:
            if model_path is None:
                raise ValueError("YuNetDetector needs a model_path or a session")
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(
                str(model_path), options, providers=["CPUExecutionProvider"]
            )
        self._session = session
        self._names = [o.name for o in session.get_outputs()]
        self.score_threshold = score_threshold
        self.nms_threshold = nms_threshold

    def detect(self, bgr: np.ndarray) -> list[Face]:
        raw = self._session.run(None, {"input": to_input_blob(bgr)})
        return decode_yunet(
            dict(zip(self._names, raw)),
            score_threshold=self.score_threshold,
            nms_threshold=self.nms_threshold,
        )


_DETECTORS: dict[str, FaceDetector] = {}
_DETECTORS_LOCK = threading.Lock()


def get_face_detector() -> FaceDetector:
    """The process-wide YuNet detector for ``settings.reframe_model_dir``.

    The first call downloads and verifies the model (network) and builds the
    onnxruntime session; call it from a worker thread, never the event loop.
    """
    model_dir = str(Path(settings.reframe_model_dir))
    with _DETECTORS_LOCK:
        detector = _DETECTORS.get(model_dir)
        if detector is None:
            detector = YuNetDetector(ensure_model(model_dir))
            _DETECTORS[model_dir] = detector
        return detector


def reset_for_tests() -> None:
    with _DETECTORS_LOCK:
        _DETECTORS.clear()
